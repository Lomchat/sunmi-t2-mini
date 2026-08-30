#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/srv/sunmi_t2_mini
SERVICE_USER=sunmi-print

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$APP_DIR/data/uploads" "$APP_DIR/data/downloads"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/data"
chmod 0750 "$APP_DIR/data"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/server/requirements.txt"
install -o root -g root -m 0644 "$APP_DIR/deploy/sunmi-t2-mini.service" /etc/systemd/system/sunmi-t2-mini.service
systemctl daemon-reload
systemctl enable --now sunmi-t2-mini.service
