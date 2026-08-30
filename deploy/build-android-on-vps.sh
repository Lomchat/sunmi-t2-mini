#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/srv/sunmi_t2_mini
SDK_ROOT="$APP_DIR/.android-sdk"
CMDLINE_VERSION=15859902
CMDLINE_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583

source "$APP_DIR/secrets/build.env"

if [ ! -x "$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" ]; then
  archive=/tmp/commandlinetools-linux-${CMDLINE_VERSION}.zip
  curl --fail --location --retry 3 \
    "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_VERSION}_latest.zip" \
    --output "$archive"
  printf '%s  %s\n' "$CMDLINE_SHA256" "$archive" | sha256sum --check --strict
  unpack_dir=$(mktemp -d)
  unzip -q "$archive" -d "$unpack_dir"
  install -d -m 0755 "$SDK_ROOT/cmdline-tools"
  mv "$unpack_dir/cmdline-tools" "$SDK_ROOT/cmdline-tools/latest"
fi

export ANDROID_HOME="$SDK_ROOT"
export ANDROID_SDK_ROOT="$SDK_ROOT"
export PATH="$SDK_ROOT/cmdline-tools/latest/bin:$PATH"

set +o pipefail
yes | sdkmanager --sdk_root="$SDK_ROOT" --licenses >/dev/null
set -o pipefail
sdkmanager --sdk_root="$SDK_ROOT" \
  "platforms;android-35" \
  "build-tools;35.0.0" \
  "platform-tools"

if [ ! -f "$APP_DIR/secrets/sunmi-release.jks" ]; then
  keytool -genkeypair -noprompt \
    -keystore "$APP_DIR/secrets/sunmi-release.jks" \
    -storepass "$KEYSTORE_PASSWORD" \
    -keypass "$KEYSTORE_PASSWORD" \
    -alias sunmi-agent \
    -keyalg RSA -keysize 4096 -validity 10000 \
    -dname "CN=SUNMI Print Agent, OU=Chalco, O=Chalco, C=IL"
  chmod 0600 "$APP_DIR/secrets/sunmi-release.jks"
fi

cd "$APP_DIR/android"
sed -i 's/\r$//' ./gradlew
./gradlew --no-daemon clean assembleRelease \
  -PserverUrl=https://sunmi.chalco.website \
  -PdeviceId=t2-mini-01 \
  -PdeviceToken="$DEVICE_TOKEN" \
  -PkeystoreFile="$APP_DIR/secrets/sunmi-release.jks" \
  -PkeystorePassword="$KEYSTORE_PASSWORD" \
  -PkeyAlias=sunmi-agent \
  -PkeyPassword="$KEYSTORE_PASSWORD"

apk="$APP_DIR/android/app/build/outputs/apk/release/app-release.apk"
test -s "$apk"
"$SDK_ROOT/build-tools/35.0.0/apksigner" verify --verbose --print-certs "$apk"
install -o sunmi-print -g sunmi-print -m 0640 \
  "$apk" "$APP_DIR/data/downloads/sunmi-printer-agent.apk"
sha256sum "$APP_DIR/data/downloads/sunmi-printer-agent.apk"
