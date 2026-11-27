from __future__ import annotations

import asyncio
import io
import json
import math
import secrets
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .db import Database
from .security import new_csrf_token, verify_password, verify_token


BASE_DIR = Path(__file__).resolve().parent


_LOGIN_MAX_FAILS = 10
_LOGIN_WINDOW = 15 * 60
_LOGIN_MAX_KEYS = 10000
_login_fails: dict[str, list[float]] = {}


def _login_recent(ip: str, now: float) -> list[float]:
    hits = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
    if hits:
        _login_fails[ip] = hits
    else:
        _login_fails.pop(ip, None)
    return hits


def _login_record_fail(ip: str) -> None:
    now = time.time()
    if len(_login_fails) >= _LOGIN_MAX_KEYS:
        for key in list(_login_fails):
            _login_recent(key, now)
        if len(_login_fails) >= _LOGIN_MAX_KEYS:
            _login_fails.pop(next(iter(_login_fails)))
    _login_fails.setdefault(ip, []).append(now)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_directories()
    database = Database(settings.database_path, settings.lease_seconds, settings.max_attempts)
    database.initialize()

    app = FastAPI(title="SUNMI T2 Mini Cloud Printer", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.database = database
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="sunmi_session",
        max_age=8 * 60 * 60,
        same_site="strict",
        https_only=settings.public_url.startswith("https://"),
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if settings.public_url.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/") or request.url.path in {
            "/",
            "/login",
            "/download/sunmi-printer-agent.apk",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response

    def current_user(request: Request) -> tuple[str, str] | None:
        username = request.session.get("username")
        role = request.session.get("role")
        if username == settings.admin_username and role == "admin":
            return username, role
        if settings.print_username and username == settings.print_username and role == "printer":
            return username, role
        return None

    def require_user(request: Request) -> tuple[str, str]:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user

    def require_admin(request: Request) -> None:
        user = require_user(request)
        if user[1] != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    def csrf_for(request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = new_csrf_token()
            request.session["csrf"] = token
        return token

    def require_csrf(request: Request, token: str | None) -> None:
        expected = request.session.get("csrf", "")
        if not expected or not token or not secrets.compare_digest(expected, token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")

    def require_device(authorization: str | None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not verify_token(authorization[7:], settings.device_token_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    def remote_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        return forwarded or (request.client.host if request.client else "")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if current_user(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"csrf": csrf_for(request), "error": ""})

    @app.post("/login", response_class=HTMLResponse)
    async def login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
        csrf: Annotated[str, Form()],
    ):
        require_csrf(request, csrf)
        # uvicorn tourne avec --proxy-headers : client.host est la vraie IP du visiteur.
        ip = request.client.host if request.client else "unknown"
        recent = _login_recent(ip, time.time())
        if len(recent) >= _LOGIN_MAX_FAILS:
            wait = max(1, math.ceil(recent[0] + _LOGIN_WINDOW - time.time()))
            minutes = max(1, math.ceil(wait / 60))
            return templates.TemplateResponse(
                request,
                "login.html",
                {"csrf": csrf_for(request), "error": f"Trop de tentatives, réessayez dans {minutes} min."},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        role = ""
        if username == settings.admin_username and await run_in_threadpool(
            verify_password, password, settings.admin_password_hash
        ):
            role = "admin"
        elif (
            settings.print_username
            and username == settings.print_username
            and await run_in_threadpool(verify_password, password, settings.print_password_hash)
        ):
            role = "printer"
        if not role:
            _login_record_fail(ip)
            await asyncio.sleep(0.4)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"csrf": csrf_for(request), "error": "Identifiants incorrects."},
                status_code=401,
            )
        _login_fails.pop(ip, None)
        request.session.clear()
        request.session["username"] = username
        request.session["role"] = role
        request.session["csrf"] = new_csrf_token()
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request, csrf: Annotated[str, Form()]):
        require_user(request)
        require_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        try:
            username, role = require_user(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "csrf": csrf_for(request),
                "device_id": settings.device_id,
                "username": username,
                "apk_available": role == "admin" and settings.apk_path.exists(),
                "can_download_apk": role == "admin",
                "can_view_history": role == "admin",
            },
        )

    @app.get("/download/sunmi-printer-agent.apk")
    async def download_apk(request: Request):
        require_admin(request)
        if not settings.apk_path.exists():
            raise HTTPException(status_code=404, detail="APK indisponible")
        return FileResponse(
            settings.apk_path,
            media_type="application/vnd.android.package-archive",
            filename="sunmi-printer-agent.apk",
        )

    @app.get("/api/admin/status")
    async def admin_status(request: Request):
        _, role = require_user(request)
        device = database.device_status(settings.device_id, settings.online_window_seconds)
        if role != "admin":
            device["queue"] = {
                "queued": device["queue"]["queued"],
                "leased": device["queue"]["leased"],
            }
        return device

    @app.get("/api/admin/jobs")
    async def admin_jobs(request: Request):
        require_admin(request)
        return {"jobs": database.list_jobs()}

    @app.post("/api/admin/jobs")
    async def create_job(
        request: Request,
        text: Annotated[str, Form()] = "",
        qr: Annotated[str, Form()] = "",
        align: Annotated[str, Form()] = "left",
        size: Annotated[int, Form()] = 28,
        bold: Annotated[bool, Form()] = False,
        text_feed: Annotated[int, Form()] = 0,
        feed: Annotated[int, Form()] = 4,
        cut: Annotated[bool, Form()] = False,
        document_bitmap: UploadFile | None = File(default=None),
        text_bitmap: UploadFile | None = File(default=None),
        image: UploadFile | None = File(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        require_user(request)
        require_csrf(request, x_csrf_token)
        align = align if align in {"left", "center", "right"} else "left"
        size = max(16, min(size, 64))
        text_feed = max(0, min(text_feed, 12))
        feed = max(0, min(feed, 12))
        blocks: list[dict] = []
        clean_text = text.strip()
        clean_qr = qr.strip()

        async def save_image(upload: UploadFile, max_height: int, require_printer_width: bool = False) -> str:
            raw = await upload.read(settings.max_upload_bytes + 1)
            if len(raw) > settings.max_upload_bytes:
                raise HTTPException(413, "Image trop volumineuse (5 Mio maximum)")
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    source.verify()
                with Image.open(io.BytesIO(raw)) as source:
                    converted = ImageOps.exif_transpose(source).convert("L")
                    if require_printer_width and converted.width != 576:
                        raise HTTPException(400, "L’aperçu texte doit mesurer 576 pixels de large")
                    if not require_printer_width and converted.width > 576:
                        new_height = max(1, round(converted.height * 576 / converted.width))
                        converted = converted.resize((576, new_height), Image.Resampling.LANCZOS)
                    if converted.height > max_height:
                        raise HTTPException(400, "L’image est trop haute pour un ticket")
                    converted = converted.point(lambda pixel: 0 if pixel < 160 else 255, mode="1")
                    asset_id = uuid.uuid4().hex
                    target = settings.upload_dir / f"{asset_id}.png"
                    converted.save(target, "PNG", optimize=True)
                    return asset_id
            except (UnidentifiedImageError, OSError):
                raise HTTPException(400, "Fichier image invalide")

        if document_bitmap and document_bitmap.filename:
            if len(clean_text) > 8000:
                raise HTTPException(400, "Le texte dépasse 8 000 caractères")
            asset_id = await save_image(document_bitmap, max_height=16_000, require_printer_width=True)
            blocks.append({"type": "image", "asset_id": asset_id, "align": "left", "source": "document"})
        elif clean_text:
            if len(clean_text) > 8000:
                raise HTTPException(400, "Le texte dépasse 8 000 caractères")
            if text_bitmap and text_bitmap.filename:
                asset_id = await save_image(text_bitmap, max_height=16_000, require_printer_width=True)
                blocks.append({"type": "image", "asset_id": asset_id, "align": "left", "source": "text"})
            else:
                blocks.append({"type": "text", "value": clean_text, "align": align, "size": size, "bold": bold})
            if text_feed:
                blocks.append({"type": "feed", "lines": text_feed})

        if image and image.filename and not (document_bitmap and document_bitmap.filename):
            asset_id = await save_image(image, max_height=4000)
            blocks.append({"type": "image", "asset_id": asset_id, "align": align})

        if clean_qr:
            if len(clean_qr) > 2048:
                raise HTTPException(400, "Le contenu QR dépasse 2 048 caractères")
            blocks.append({"type": "qr", "value": clean_qr, "align": align, "module_size": 7})
        if feed:
            blocks.append({"type": "feed", "lines": feed})
        if cut:
            blocks.append({"type": "cut"})
        if not blocks or all(block["type"] in {"feed", "cut"} for block in blocks):
            raise HTTPException(400, "Ajoute du texte, une image ou un QR code")

        summary = clean_text.replace("\n", " ")[:70] or (image.filename if image and image.filename else "QR code")
        job_id = database.create_job({"summary": summary, "blocks": blocks})
        return JSONResponse({"id": job_id, "state": "queued"}, status_code=201)

    @app.post("/api/admin/jobs/{job_id}/retry")
    async def retry_job(job_id: str, request: Request, x_csrf_token: str | None = Header(default=None)):
        require_admin(request)
        require_csrf(request, x_csrf_token)
        if not database.retry(job_id):
            raise HTTPException(409, "Seuls les travaux en échec peuvent être relancés")
        return {"ok": True}

    @app.post("/api/device/heartbeat")
    async def heartbeat(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_device(authorization)
        body = await request.json()
        device_id = str(body.get("device_id", ""))
        if device_id != settings.device_id:
            raise HTTPException(403, "Appareil inconnu")
        database.touch_device(
            device_id,
            str(body.get("app_version", "")),
            str(body.get("device_model", "")),
            str(body.get("printer_status", "unknown")),
            str(body.get("last_error", "")),
            remote_ip(request),
        )
        return {"ok": True, "server_time": time.time()}

    @app.get("/api/device/jobs/next")
    async def next_job(
        request: Request,
        device_id: str,
        wait: int = 25,
        authorization: str | None = Header(default=None),
        x_app_version: str = Header(default=""),
        x_device_model: str = Header(default=""),
        x_printer_status: str = Header(default="unknown"),
        x_last_error: str = Header(default=""),
    ):
        require_device(authorization)
        if device_id != settings.device_id:
            raise HTTPException(403, "Appareil inconnu")
        wait = max(0, min(wait, 30))
        database.touch_device(device_id, x_app_version, x_device_model, x_printer_status, x_last_error, remote_ip(request))
        deadline = time.monotonic() + wait
        while True:
            job = database.lease_next_job()
            if job:
                return job
            if await request.is_disconnected() or time.monotonic() >= deadline:
                return Response(status_code=204)
            await asyncio.sleep(0.5)

    @app.get("/api/device/assets/{asset_id}")
    async def device_asset(asset_id: str, authorization: str | None = Header(default=None)):
        require_device(authorization)
        if not asset_id.isalnum() or len(asset_id) != 32:
            raise HTTPException(404)
        path = settings.upload_dir / f"{asset_id}.png"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/device/jobs/{job_id}/result")
    async def job_result(job_id: str, request: Request, authorization: str | None = Header(default=None)):
        require_device(authorization)
        body = await request.json()
        lease_token = str(body.get("lease_token", ""))
        success = body.get("status") == "printed"
        error = str(body.get("error", ""))
        if not lease_token:
            raise HTTPException(400, "lease_token requis")
        if not database.acknowledge(job_id, lease_token, success, error):
            raise HTTPException(409, "Bail invalide ou déjà finalisé")
        return {"ok": True}

    return app
