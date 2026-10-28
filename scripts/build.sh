#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
APP='.build/package/Voice App.app'
voice_app_install=false
case "${1-}" in
    '') [ "$#" -eq 0 ] || { echo 'Usage: sh scripts/build.sh [--install]' >&2; exit 2; } ;;
    --install) [ "$#" -eq 1 ] || { echo 'Usage: sh scripts/build.sh [--install]' >&2; exit 2; }; voice_app_install=true ;;
    *) echo 'Usage: sh scripts/build.sh [--install]' >&2; exit 2 ;;
esac
# Ordinary builds never request access to the signing key or replace the installed app.
if [ "$voice_app_install" = true ]; then
    if /usr/bin/pgrep -x voice-app >/dev/null 2>&1; then
        echo 'Quit Voice App before installing an update.' >&2
        exit 1
    fi
    # A certificate-backed identity preserves the designated requirement across builds.
    # Check before touching the existing app so a missing identity cannot replace it.
    voice_app_signing_identity=$(/usr/bin/security find-identity -v -p codesigning | /usr/bin/awk '/"Voice App Local Development"$/ { print $2 }')
    if [ -z "$voice_app_signing_identity" ]; then
        echo 'Missing code-signing identity: Voice App Local Development.' >&2
        echo 'Create the approved local Code Signing identity in your login keychain before rebuilding.' >&2
        echo 'The existing app has been left untouched. Ad-hoc signing is disabled.' >&2
        exit 1
    fi
    if [ "$(printf '%s\n' "$voice_app_signing_identity" | /usr/bin/wc -l | /usr/bin/tr -d ' ')" != 1 ]; then
        echo 'More than one Voice App Local Development identity exists. Select a single identity before rebuilding.' >&2
        exit 1
    fi
fi
sh scripts/cargo.sh build --offline --locked
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"
/usr/bin/xcrun swiftc -target arm64-apple-macosx14.0 -swift-version 5 -O -emit-library -module-name VoiceDesktop -Xlinker -install_name -Xlinker @rpath/libVoiceDesktop.dylib -framework AppKit -framework AVFoundation -framework ApplicationServices -framework ScreenCaptureKit Sources/ActionGate.swift Sources/AgentCursor.swift Sources/DesktopWorker.swift Sources/DesktopAdapter.swift -o "$APP/Contents/Frameworks/libVoiceDesktop.dylib"
cp .build/rust-target/debug/voice-app "$APP/Contents/MacOS/voice-app"
cp Sources/bridge.py "$APP/Contents/Resources/bridge.py"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>voice-app</string>
<key>CFBundleIdentifier</key><string>tech.kennezekiel.voice-app</string>
<key>CFBundleName</key><string>Voice App</string>
<key>CFBundleVersion</key><string>2</string>
<key>CFBundleShortVersionString</key><string>0.2</string>
<key>NSMicrophoneUsageDescription</key><string>Voice App sends audio through your installed Codex native voice runtime to OpenAI using your ChatGPT account. Opening Voice App starts a session and microphone access. Stop session turns it off.</string>
<key>NSAccessibilityUsageDescription</key><string>Voice App observes app controls and performs mouse and keyboard actions for your spoken desktop tasks while desktop sharing is enabled.</string>
<key>NSScreenCaptureUsageDescription</key><string>Voice App sends display screenshots to Codex to understand your desktop and verify actions. Opening Voice App starts a session with desktop sharing. Stop session turns it off.</string>
<key>NSHighResolutionCapable</key><true/>
<key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
PLIST
/usr/bin/plutil -lint "$APP/Contents/Info.plist"
if [ "$voice_app_install" = false ]; then
    echo "Prepared development build: $PWD/$APP"
    echo 'Signing and installation skipped. The installed app is unchanged.'
    exit 0
fi
/usr/bin/codesign --force --sign "$voice_app_signing_identity" --timestamp=none "$APP/Contents/Frameworks/libVoiceDesktop.dylib"
/usr/bin/codesign --force --sign "$voice_app_signing_identity" --timestamp=none "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
if /usr/bin/pgrep -x voice-app >/dev/null 2>&1; then
    echo "Signed package ready at: $PWD/$APP" >&2
    echo 'Quit Voice App before replacing its running app bundle.' >&2
    exit 1
fi
/usr/bin/ditto "$APP" '.build/Voice App.app'
/usr/bin/codesign --verify --deep --strict '.build/Voice App.app'
echo "Built: $PWD/.build/Voice App.app"
