import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.security import hash_password, hash_token


def build_client(tmp_path: Path) -> tuple[TestClient, str]:
    token = "test-device-token"
    settings = Settings(
        data_dir=tmp_path,
        admin_username="admin",
        admin_password_hash=hash_password("correct-horse"),
        session_secret="test-session-secret-that-is-long-enough",
        device_token_hash=hash_token(token),
        device_id="t2-mini-01",
        public_url="http://testserver",
        print_username="operator",
        print_password_hash=hash_password("test-print-password"),
        lease_seconds=2,
        max_attempts=3,
    )
    return TestClient(create_app(settings)), token


def login(client: TestClient, username: str = "admin", password: str = "correct-horse") -> str:
    page = client.get("/login")
    assert page.status_code == 200
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    dashboard = client.get("/")
    return dashboard.text.split('data-csrf="', 1)[1].split('"', 1)[0]


def test_authentication_required(tmp_path: Path):
    client, _ = build_client(tmp_path)
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/admin/status").status_code == 401


def test_image_preview_blob_is_allowed_by_content_security_policy(tmp_path: Path):
    client, _ = build_client(tmp_path)
    response = client.get("/login")
    assert "img-src 'self' data: blob:" in response.headers["content-security-policy"]


def test_end_to_end_queue_flow(tmp_path: Path):
    client, device_token = build_client(tmp_path)
    csrf = login(client)
    assert "preview-with-history" in client.get("/").text
    created = client.post(
        "/api/admin/jobs",
        data={"text": "Bonjour", "align": "center", "size": "28", "feed": "3", "cut": "true"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    headers = {
        "Authorization": f"Bearer {device_token}",
        "X-App-Version": "1.0.0",
        "X-Device-Model": "SUNMI T2 MINI",
        "X-Printer-Status": "ready",
    }
    leased = client.get("/api/device/jobs/next?device_id=t2-mini-01&wait=0", headers=headers)
    assert leased.status_code == 200
    assert leased.json()["id"] == job_id
    assert leased.json()["payload"]["blocks"][0]["value"] == "Bonjour"

    result = client.post(
        f"/api/device/jobs/{job_id}/result",
        headers={"Authorization": f"Bearer {device_token}"},
        json={"lease_token": leased.json()["lease_token"], "status": "printed"},
    )
    assert result.status_code == 200
    jobs = client.get("/api/admin/jobs").json()["jobs"]
    assert jobs[0]["state"] == "printed"


def test_print_user_can_create_jobs_but_not_view_history_or_download_apk(tmp_path: Path):
    client, _ = build_client(tmp_path)
    csrf = login(client, "operator", "test-print-password")
    dashboard = client.get("/")
    assert "APERÇU EN DIRECT" in dashboard.text
    assert 'id="receipt-preview"' in dashboard.text
    assert "preview-with-history" not in dashboard.text
    assert "Dernières impressions" not in dashboard.text
    assert "count-printed" not in dashboard.text
    created = client.post(
        "/api/admin/jobs",
        data={"text": "Operator print"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert client.get("/api/admin/jobs").status_code == 403
    status_response = client.get("/api/admin/status")
    assert status_response.status_code == 200
    assert "printed" not in status_response.json()["queue"]
    assert "failed" not in status_response.json()["queue"]
    assert client.get("/download/sunmi-printer-agent.apk").status_code == 403


def test_browser_text_bitmap_is_the_asset_sent_to_printer(tmp_path: Path):
    client, device_token = build_client(tmp_path)
    csrf = login(client, "operator", "test-print-password")
    bitmap = io.BytesIO()
    Image.new("1", (576, 80), 1).save(bitmap, "PNG")
    created = client.post(
        "/api/admin/jobs",
        data={"text": "Texte figé", "feed": "0", "cut": "false"},
        files={"text_bitmap": ("texte-rasterise.png", bitmap.getvalue(), "image/png")},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    leased = client.get(
        "/api/device/jobs/next?device_id=t2-mini-01&wait=0",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert leased.status_code == 200
    first_block = leased.json()["payload"]["blocks"][0]
    assert first_block["type"] == "image"
    assert first_block["source"] == "text"


def test_unchecked_cut_box_does_not_add_cut_operation(tmp_path: Path):
    client, device_token = build_client(tmp_path)
    csrf = login(client, "operator", "test-print-password")
    created = client.post(
        "/api/admin/jobs",
        data={"text": "Sans découpe", "feed": "2"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    leased = client.get(
        "/api/device/jobs/next?device_id=t2-mini-01&wait=0",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert leased.status_code == 200
    block_types = [block["type"] for block in leased.json()["payload"]["blocks"]]
    assert "cut" not in block_types


def test_blank_lines_after_text_are_inserted_before_image(tmp_path: Path):
    client, device_token = build_client(tmp_path)
    csrf = login(client, "operator", "test-print-password")
    text_bitmap = io.BytesIO()
    photo = io.BytesIO()
    Image.new("1", (576, 80), 1).save(text_bitmap, "PNG")
    Image.new("RGB", (200, 100), "white").save(photo, "PNG")
    created = client.post(
        "/api/admin/jobs",
        data={"text": "Ligne 1\n\nLigne 3", "text_feed": "3", "feed": "0"},
        files={
            "text_bitmap": ("texte.png", text_bitmap.getvalue(), "image/png"),
            "image": ("photo.png", photo.getvalue(), "image/png"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    leased = client.get(
        "/api/device/jobs/next?device_id=t2-mini-01&wait=0",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    blocks = leased.json()["payload"]["blocks"]
    assert [block["type"] for block in blocks] == ["image", "feed", "image"]
    assert blocks[1]["lines"] == 3


def test_rich_document_bitmap_is_printed_as_one_exact_block(tmp_path: Path):
    client, device_token = build_client(tmp_path)
    csrf = login(client, "operator", "test-print-password")
    document = io.BytesIO()
    Image.new("1", (576, 240), 1).save(document, "PNG")
    created = client.post(
        "/api/admin/jobs",
        data={"text": "Composition enrichie", "feed": "0", "cut": "false"},
        files={"document_bitmap": ("composition.png", document.getvalue(), "image/png")},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    leased = client.get(
        "/api/device/jobs/next?device_id=t2-mini-01&wait=0",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    blocks = leased.json()["payload"]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"] == "document"


def test_rejects_wrong_print_user_password(tmp_path: Path):
    client, _ = build_client(tmp_path)
    page = client.get("/login")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/login",
        data={"username": "operator", "password": "wrong", "csrf": csrf},
    )
    assert response.status_code == 401


def test_rejects_bad_device_token_and_csrf(tmp_path: Path):
    client, _ = build_client(tmp_path)
    csrf = login(client)
    assert client.get(
        "/api/device/jobs/next?device_id=t2-mini-01&wait=0",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401
    assert client.post(
        "/api/admin/jobs",
        data={"text": "test"},
        headers={"X-CSRF-Token": csrf + "bad"},
    ).status_code == 403
