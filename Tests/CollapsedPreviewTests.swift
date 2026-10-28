// Compile in the same file as DesktopAdapter.swift to check its private preview state.
@main struct CollapsedPreviewTests {
    static func main() {
        precondition(Thread.isMainThread)
        let captions = CompactCaptionsView(frame: NSRect(x: 0, y: 0, width: 336, height: 180))
        let longSpeech = String(repeating: "Older words that should scroll away. ", count: 20) + "NEWEST WORDS STAY VISIBLE"
        captions.update([("You", longSpeech)], request: "long", appearing: false)
        captions.stopAnimation()
        let longLayout = captions.bodyLayout(longSpeech, width: 320, index: 0)
        precondition(longLayout.offset > 0 && abs(longLayout.height - longLayout.offset - 92) < 0.1)
        captions.update([("You", "Short request")], request: "short", appearing: false)
        precondition(captions.bodyLayout("Short request", width: 320, index: 0).offset == 0)
        captions.update([("Agent", longSpeech)], request: "reply", appearing: false)
        precondition(captions.bodyLayout(longSpeech, width: 320, index: 0).offset == 0)
        captions.update([("You", longSpeech)], request: "long", appearing: false)
        captions.stopAnimation()
        let captionImage = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 336, pixelsHigh: 180,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        let bitmapContext = NSGraphicsContext(bitmapImageRep: captionImage)!
        NSGraphicsContext.current = NSGraphicsContext(cgContext: bitmapContext.cgContext, flipped: true)
        let flip = NSAffineTransform(); flip.translateX(by: 0, yBy: 180); flip.scaleX(by: 1, yBy: -1); flip.concat()
        captions.draw(captions.bounds)
        NSGraphicsContext.restoreGraphicsState()
        try! captionImage.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: ".build/caption-tail-preview.png"))
        print("Caption overflow: newest words retained, short text unchanged, replies retain their reading start.")
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 12, pixelsHigh: 8,
            bitsPerSample: 8, samplesPerPixel: 3, hasAlpha: false, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        let jpeg = bitmap.representation(using: .jpeg, properties: [:])!
        let result = controller.control(["operation":"snapshot", "observation":[
            "screenshot":"data:image/jpeg;base64," + jpeg.base64EncodedString(),
            "app":"Disposable test image", "observedAt": 1000.0]])
        precondition(result["success"] as? Bool == true)
        precondition(controller.previewImage != nil)
        precondition(controller.previewLabel.contains("Disposable test image"))
        precondition(controller.previewLabel.contains("Snapshot"))
        precondition(controller.previewPanel == nil) // Receiving a capture never opens UI.
        _ = controller.control(["operation":"preview", "visible":false])
        precondition(!controller.previewVisible)
        _ = controller.control(["operation":"share", "enabled":false])
        precondition(controller.previewImage == nil && !controller.previewVisible)
        precondition(controller.previewLabel == "Waiting for a desktop capture…")
        _ = controller.control(["operation":"snapshot", "observation":["screenshot":"invalid"]])
        precondition(controller.previewImage == nil)
        let complete = ["user":"Open the drawer", "agent":"Opened", "request":"1", "phase":"complete"]
        controller.updateCaptions(complete)
        let deadline = controller.captionDeadline!
        controller.dismissExpiredCaptions(now: deadline - 0.01)
        precondition(!controller.captionsDismissed)
        controller.dismissExpiredCaptions(now: deadline)
        precondition(controller.captionsDismissed)
        controller.updateCaptions(complete)
        controller.positionCaptions()
        precondition(controller.captionsDismissed) // Renders and layout cannot revive an expired reply.
        controller.updateCaptions(["user":"Next request", "request":"2", "phase":"active"])
        controller.dismissExpiredCaptions(now: deadline + 100)
        precondition(controller.captionDeadline == nil && !controller.captionsDismissed)
        controller.updateCaptions(["agent":"Done", "request":"2", "phase":"complete"])
        precondition(controller.captionDeadline != nil && !controller.captionsDismissed)
        controller.updateCaptions([:])
        controller.dismissExpiredCaptions(now: deadline + 100)
        precondition(controller.captionDeadline == nil && !controller.captionsDismissed)
        controller.updateCaptions(complete)
        let started = ProcessInfo.processInfo.systemUptime
        while !controller.captionsDismissed && ProcessInfo.processInfo.systemUptime - started < 6 {
            RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.02))
        }
        let elapsed = ProcessInfo.processInfo.systemUptime - started
        precondition(controller.captionsDismissed && elapsed >= 5 && elapsed < 5.5)
        controller.updateCaptions([:])
        print("Actual caption timer dismissed after \(String(format: "%.2f", elapsed)) seconds.")
        print("Caption dismissal: five-second boundary, redraw persistence, live request preservation, new reply and Stop cleanup passed.")
        print("Collapsed preview: actual image decoding, snapshot label, no automatic popup, hover dismissal, sharing-off cleanup and invalid image handling passed. No desktop captured or input posted.")
    }
}
