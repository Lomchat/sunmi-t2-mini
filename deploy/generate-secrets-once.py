from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path


ENV_PATH = Path("/etc/sunmi-t2-mini.env")
BUILD_ENV_PATH = Path("/srv/sunmi_t2_mini/secrets/build.env")


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return "pbkdf2_sha256$310000${}${}".format(
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


if ENV_PATH.exists() or BUILD_ENV_PATH.exists():
    raise SystemExit("Refusing to replace existing production secrets.")

admin_password = secrets.token_urlsafe(18)
device_token = secrets.token_urlsafe(32)
session_secret = secrets.token_urlsafe(48)
keystore_password = secrets.token_urlsafe(24)

ENV_PATH.write_text(
    "\n".join(
        [
            "SUNMI_DATA_DIR=/srv/sunmi_t2_mini/data",
            "SUNMI_PUBLIC_URL=https://sunmi.chalco.website",
            "SUNMI_ADMIN_USERNAME=admin",
            f"SUNMI_ADMIN_PASSWORD_HASH={password_hash(admin_password)}",
            "SUNMI_DEVICE_ID=t2-mini-01",
            f"SUNMI_DEVICE_TOKEN_HASH={hashlib.sha256(device_token.encode()).hexdigest()}",
            f"SUNMI_SESSION_SECRET={session_secret}",
            "SUNMI_ONLINE_WINDOW_SECONDS=75",
            "SUNMI_LEASE_SECONDS=90",
            "SUNMI_MAX_ATTEMPTS=5",
            "",
        ]
    ),
    encoding="utf-8",
)
BUILD_ENV_PATH.write_text(
    f"DEVICE_TOKEN={device_token}\nKEYSTORE_PASSWORD={keystore_password}\n",
    encoding="utf-8",
)
os.chmod(ENV_PATH, 0o600)
os.chmod(BUILD_ENV_PATH, 0o600)
print("ADMIN_USERNAME=admin")
print(f"ADMIN_PASSWORD={admin_password}")
