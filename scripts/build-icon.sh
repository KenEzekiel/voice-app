#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
iconset='.build/VoiceApp.iconset'
output=${1:-.build/VoiceApp.icns}
mkdir -p "$iconset" "$(dirname "$output")"
for points in 16 32 128 256 512; do
    /usr/bin/sips -z "$points" "$points" Assets/voice-app-icon.png --out "$iconset/icon_${points}x${points}.png" >/dev/null
    pixels=$((points * 2))
    /usr/bin/sips -z "$pixels" "$pixels" Assets/voice-app-icon.png --out "$iconset/icon_${points}x${points}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$iconset" -o "$output"
