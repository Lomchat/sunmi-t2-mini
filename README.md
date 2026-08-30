# SUNMI T2 Mini Cloud Printer

A self-hosted remote printing system for the built-in thermal printer in a
SUNMI T2 Mini POS terminal.

An authenticated web dashboard submits printable documents to a durable queue.
A small Android agent installed on the terminal maintains an outbound HTTPS
connection, retrieves queued jobs, prints them through SUNMI's printer service,
and reports the result. The terminal does not need root access, an inbound port,
Google Play Services, Firebase, or a VPN.

> This is an independent open-source project and is not affiliated with or
> endorsed by SUNMI. SUNMI is a trademark of its respective owner.

## Features

- Password-protected, server-rendered dashboard.
- Separate administrator and print-only accounts.
- Rich ticket editor with live 576-pixel preview, formatted text, alignment,
  images, image resizing, QR codes, line feeds, and optional paper cutting.
- Server-side validation and monochrome conversion for thermal printing.
- SQLite-backed durable queue with `queued`, `leased`, `printed`, and `failed`
  states.
- At-least-once delivery, expiring leases, bounded retries, and idempotent job
  acknowledgements.
- Online/offline terminal status based on heartbeats.
- Android 7.1/API 25-compatible agent with long polling, a foreground service,
  automatic retry, and restart after boot.
- Authenticated APK download from the administrator dashboard.
- HTTPS-oriented production deployment with systemd and Caddy examples.

## Architecture

```text
Browser
   |  HTTPS + session authentication
   v
FastAPI dashboard/API ---- SQLite queue + validated image assets
   ^
   |  outbound HTTPS + device bearer token
   |
SUNMI T2 Mini Android agent ---- built-in SUNMI printer service
```

The Android terminal always initiates the connection. It sends heartbeats and
long-polls for work; no device or Android debugging port should be exposed to
the internet.

## Repository layout

- `server/` — FastAPI application, HTML dashboard, device API, SQLite queue,
  and tests.
- `android/` — Java Android agent and Gradle wrapper.
- `deploy/` — systemd, Caddy, server installation, secret generation, and APK
  build helpers for the reference VPS deployment.
- `.env.example` — documented configuration with placeholders only.

Runtime data, uploaded images, APK files, databases, signing keys, and real
environment files are deliberately excluded from Git.

## Requirements

For the server:

- Python 3.11 or newer.
- A reverse proxy providing HTTPS in production; the included example uses
  Caddy.
- Linux with systemd if using the provided production service.

For the Android agent:

- A SUNMI terminal exposing the SUNMI printer service.
- JDK 17 and Android SDK platform 35 to build the app.
- Android 5.0/API 21 or newer at runtime. The app targets API 25 to remain
  compatible with older SUNMI firmware.

## Local server setup

Create an isolated Python environment and install the development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r server/requirements-dev.txt
```

Generate an administrator password hash, device token hash, and random session
secret. Keep the original device token because the Android build needs it:

```bash
export PYTHONPATH=server
python -m app.security 'replace-this-admin-password'
python -m app.security --token 'replace-this-device-token'
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Copy the values into environment variables using `.env.example` as a reference.
For a local development session:

```bash
export SUNMI_DATA_DIR="$PWD/data"
export SUNMI_PUBLIC_URL=http://127.0.0.1:8000
export SUNMI_ADMIN_USERNAME=admin
export SUNMI_ADMIN_PASSWORD_HASH='pbkdf2_sha256$...'
export SUNMI_SESSION_SECRET='replace-with-generated-session-secret'
export SUNMI_DEVICE_ID=t2-mini-01
export SUNMI_DEVICE_TOKEN_HASH='replace-with-generated-sha256-hash'

uvicorn app.main:create_app --app-dir server --factory --reload
```

Open `http://127.0.0.1:8000/` and sign in with the administrator username and
the plain password used to create the hash.

### Optional print-only account

Set both of these variables to enable a restricted account that may submit print
jobs but cannot access job history, retry failed jobs, or download the APK:

```bash
export SUNMI_PRINT_USERNAME=operator
export SUNMI_PRINT_PASSWORD_HASH='pbkdf2_sha256$...'
```

Generate its password hash with the same `python -m app.security PASSWORD`
command. Never store the plain password in the repository.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SUNMI_ADMIN_PASSWORD_HASH` | Yes | — | PBKDF2 hash for the administrator account. |
| `SUNMI_SESSION_SECRET` | Yes | — | Random secret used to sign browser sessions. |
| `SUNMI_DEVICE_TOKEN_HASH` | Yes | — | SHA-256 hash of the Android agent bearer token. |
| `SUNMI_DATA_DIR` | No | `/srv/sunmi_t2_mini/data` | SQLite database, uploaded assets, and published APK. |
| `SUNMI_PUBLIC_URL` | No | Reference deployment URL | Public HTTPS origin of the dashboard. |
| `SUNMI_ADMIN_USERNAME` | No | `admin` | Administrator login name. |
| `SUNMI_PRINT_USERNAME` | No | Empty | Optional print-only login name. |
| `SUNMI_PRINT_PASSWORD_HASH` | No | Empty | PBKDF2 hash for the print-only account. |
| `SUNMI_DEVICE_ID` | No | `t2-mini-01` | Expected terminal identifier. |
| `SUNMI_ONLINE_WINDOW_SECONDS` | No | `75` | Maximum heartbeat age for showing the device online. |
| `SUNMI_LEASE_SECONDS` | No | `90` | Time before an unacknowledged job lease expires. |
| `SUNMI_MAX_ATTEMPTS` | No | `5` | Maximum automatic print attempts per job. |

## Android build

The server URL, device identifier, and plain device token are injected at build
time. A debug build can be produced with:

```bash
cd android
./gradlew assembleDebug \
  -PserverUrl=https://printer.example.com \
  -PdeviceId=t2-mini-01 \
  -PdeviceToken='replace-with-the-plain-device-token'
```

For a signed release build:

```bash
./gradlew assembleRelease \
  -PserverUrl=https://printer.example.com \
  -PdeviceId=t2-mini-01 \
  -PdeviceToken='replace-with-the-plain-device-token' \
  -PkeystoreFile=/secure/path/release.jks \
  -PkeystorePassword='replace-me' \
  -PkeyAlias=sunmi-agent \
  -PkeyPassword='replace-me'
```

The APK is written below `android/app/build/outputs/apk/`. Treat release APKs as
sensitive operational artifacts because they contain the device token. Do not
commit an APK or signing key.

After sideloading the APK on the terminal, open it once. The foreground service
then reconnects automatically and is restarted after a device reboot.

## Production deployment

The files in `deploy/` target the reference layout
`/srv/sunmi_t2_mini`. Review and adapt the hostname, paths, service user, device
identifier, and Caddy imports before running them on another host.

A typical deployment is:

1. Clone the repository into `/srv/sunmi_t2_mini`.
2. Create `/etc/sunmi-t2-mini.env` with mode `0600`, based on `.env.example`.
3. Run `deploy/install-server.sh` as root to create the service account, Python
   environment, and systemd service.
4. Add the reviewed Caddy configuration and reload Caddy.
5. Store Android build credentials outside Git, then build and publish the
   signed APK with `deploy/build-android-on-vps.sh`.
6. Verify `https://your-host.example/health`, dashboard authentication, terminal
   heartbeats, and an end-to-end test print.

The helper `deploy/generate-secrets-once.py` is intentionally conservative: it
refuses to overwrite existing production secrets. It contains reference
deployment paths and a hostname, so inspect it before use.

## Print delivery model

1. An authenticated browser submits a validated print document.
2. The server persists it as a queued job.
3. The Android agent authenticates, sends a heartbeat, and long-polls for work.
4. The server atomically leases one job to the configured device.
5. The agent downloads authenticated assets and prints each block in order.
6. The agent acknowledges success or failure with the lease token.
7. An expired lease becomes available again until the attempt limit is reached.

This provides at-least-once delivery. A terminal may physically print a job and
lose connectivity before acknowledging it, so duplicate output remains possible
in that failure window.

## Testing

Run the server test suite from the repository root:

```bash
source .venv/bin/activate
PYTHONPATH=server pytest -q server/tests
```

Validate the dashboard JavaScript and build the Android debug APK with:

```bash
node --check server/app/static/app.js
cd android && ./gradlew assembleDebug
```

## Security notes

- Use HTTPS in production.
- Use different high-entropy values for the session secret, device token, user
  passwords, and Android signing-key password.
- Keep `/etc/sunmi-t2-mini.env`, `secrets/`, `data/`, APKs, and keystores out of
  source control and restrict their filesystem permissions.
- The device token is revocable independently from browser passwords.
- Do not expose ADB, the SUNMI terminal, the printer service, SQLite, or the
  FastAPI development server directly to the public internet.
- Review logs and backups so that authorization headers, cookies, tokens, and
  uploaded documents are not copied into less protected systems.
- Change the example hostnames, identifiers, and credentials before deployment.

If you discover a security issue, report it privately to the repository owner
instead of opening a public issue containing exploit details or credentials.

## Limitations

- Thermal output is monochrome; color images are converted before printing.
- The ticket renderer targets the T2 Mini's 576-pixel print width.
- Printer behavior can vary across SUNMI firmware versions.
- The Android app is designed for controlled sideloading, not Google Play
  distribution.

## Contributing

Issues and pull requests are welcome. Keep changes compatible with API 25,
avoid adding secrets or generated artifacts, and include tests for queue,
authentication, or rendering behavior when applicable.

## License

This project is available under the [MIT License](LICENSE).
