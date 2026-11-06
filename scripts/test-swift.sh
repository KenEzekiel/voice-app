#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p .build/tests
xcrun swiftc -target arm64-apple-macosx14.0 -swift-version 5 -O -warnings-as-errors Sources/ActionGate.swift Tests/GateTests.swift -o .build/tests/gate
.build/tests/gate
xcrun swiftc -target arm64-apple-macosx14.0 -swift-version 5 -O -warnings-as-errors -framework Speech Sources/LocalSpeech.swift Tests/LocalSpeechBufferTests.swift -o .build/tests/speech
.build/tests/speech
xcrun swiftc -target arm64-apple-macosx14.0 -swift-version 5 -O -warnings-as-errors -framework AppKit -framework ApplicationServices -framework ScreenCaptureKit Sources/ActionGate.swift Sources/AgentCursor.swift Sources/DesktopWorker.swift Tests/DesktopWorkerTests.swift -o .build/tests/desktop
.build/tests/desktop
# The preview test intentionally shares the adapter's private controller state.
cat Sources/DesktopAdapter.swift Tests/CollapsedPreviewTests.swift > .build/tests/PreviewTests.swift
xcrun swiftc -target arm64-apple-macosx14.0 -swift-version 5 -O -warnings-as-errors -parse-as-library -framework AppKit -framework AVFoundation -framework ApplicationServices -framework ScreenCaptureKit -framework CoreAudio -framework Speech Sources/LocalSpeech.swift Sources/ActionGate.swift Sources/AgentCursor.swift Sources/DesktopWorker.swift .build/tests/PreviewTests.swift -o .build/tests/preview
.build/tests/preview
