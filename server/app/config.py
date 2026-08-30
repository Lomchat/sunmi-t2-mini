from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    admin_username: str
    admin_password_hash: str
    session_secret: str
    device_token_hash: str
    device_id: str
    public_url: str
    print_username: str = ""
    print_password_hash: str = ""
    online_window_seconds: int = 75
    lease_seconds: int = 90
    max_attempts: int = 5
    max_upload_bytes: int = 5 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.data_dir / "sunmi.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def apk_path(self) -> Path:
        return self.data_dir / "downloads" / "sunmi-printer-agent.apk"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.apk_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    data_dir = Path(os.getenv("SUNMI_DATA_DIR", "/srv/sunmi_t2_mini/data"))
    settings = Settings(
        data_dir=data_dir,
        admin_username=os.getenv("SUNMI_ADMIN_USERNAME", "admin"),
        admin_password_hash=os.environ["SUNMI_ADMIN_PASSWORD_HASH"],
        session_secret=os.environ["SUNMI_SESSION_SECRET"],
        device_token_hash=os.environ["SUNMI_DEVICE_TOKEN_HASH"],
        device_id=os.getenv("SUNMI_DEVICE_ID", "t2-mini-01"),
        public_url=os.getenv("SUNMI_PUBLIC_URL", "https://sunmi.chalco.website").rstrip("/"),
        print_username=os.getenv("SUNMI_PRINT_USERNAME", ""),
        print_password_hash=os.getenv("SUNMI_PRINT_PASSWORD_HASH", ""),
        online_window_seconds=int(os.getenv("SUNMI_ONLINE_WINDOW_SECONDS", "75")),
        lease_seconds=int(os.getenv("SUNMI_LEASE_SECONDS", "90")),
        max_attempts=int(os.getenv("SUNMI_MAX_ATTEMPTS", "5")),
    )
    settings.ensure_directories()
    return settings
