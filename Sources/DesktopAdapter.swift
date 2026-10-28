import AppKit
import ApplicationServices
import AVFoundation

struct CaptionEntrance {
    static let duration = 0.2
    static func progress(elapsed: TimeInterval, reducedMotion: Bool) -> CGFloat {
        if reducedMotion { return 1 }
        let t = min(1, max(0, elapsed / duration))
        return CGFloat(1 - pow(1 - t, 3))
    }
}

private final class CompactCaptionsView: NSView {
    override var isFlipped: Bool { true }
    var bubbles: [(String, String)] = []
    private var request = ""
    private var entrances: [String: TimeInterval] = [:]
    private var animation: Timer?
    func stopAnimation() {
        animation?.invalidate(); animation = nil
        entrances.removeAll()
    }
    func update(_ next: [(String, String)], request: String, appearing: Bool) {
        let now = ProcessInfo.processInfo.systemUptime
        let oldRoles = Set(bubbles.map { $0.0 == "You" ? "user" : "agent" })
        let roles = Set(next.map { $0.0 == "You" ? "user" : "agent" })
        let newRequest = self.request != request
        self.request = request
        bubbles = next
        entrances = entrances.filter { roles.contains($0.key) }
        for role in roles where appearing || newRequest || !oldRoles.contains(role) {
            entrances[role] = now
        }
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            stopAnimation()
        } else if !entrances.isEmpty && animation == nil {
            animation = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                let now = ProcessInfo.processInfo.systemUptime
                self.entrances = self.entrances.filter { now - $0.value < CaptionEntrance.duration }
                self.needsDisplay = true
                if self.entrances.isEmpty { self.stopAnimation() }
            }
            if let timer = animation { RunLoop.main.add(timer, forMode: .common) }
        }
        needsDisplay = true
    }
    private let bodyStyle: [NSAttributedString.Key: Any] = {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 2
        return [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor(calibratedWhite: 0.95, alpha: 1),
                .paragraphStyle: paragraph]
    }()
    private func bodyHeight(_ text: String, width: CGFloat, index: Int) -> CGFloat {
        min(index == 0 ? 92 : 140, ceil((text as NSString).boundingRect(
            with: NSSize(width: width - 28, height: 10000), options: [.usesLineFragmentOrigin],
            attributes: bodyStyle).height))
    }
    func bodyLayout(_ text: String, width: CGFloat, index: Int) -> (height: CGFloat, offset: CGFloat) {
        let visible = bodyHeight(text, width: width, index: index)
        let full = ceil((text as NSString).boundingRect(
            with: NSSize(width: width - 28, height: 1000000), options: [.usesLineFragmentOrigin],
            attributes: bodyStyle).height)
        // Live speech follows the newest words; replies retain their normal reading start.
        let followsEnd = bubbles[index].0 == "You"
        return (followsEnd ? max(visible, full) : visible, followsEnd ? max(0, full - visible) : 0)
    }
    func height(width: CGFloat) -> CGFloat {
        bubbles.enumerated().reduce(CGFloat(0)) { $0 + bodyHeight($1.element.1, width: width, index: $1.offset) + 46 }
            - (bubbles.isEmpty ? 0 : 8)
    }
    override func draw(_ dirtyRect: NSRect) {
        var y: CGFloat = 0
        let width = bounds.width - 16 // Space for the entrance slide, without clipping its right edge.
        for (index, bubble) in bubbles.enumerated() {
            let role = bubble.0 == "You" ? "user" : "agent"
            let progress = entrances[role].map { CaptionEntrance.progress(
                elapsed: ProcessInfo.processInfo.systemUptime - $0,
                reducedMotion: NSWorkspace.shared.accessibilityDisplayShouldReduceMotion) } ?? 1
            let scale = 0.96 + 0.04 * progress
            NSGraphicsContext.saveGraphicsState()
            let transform = NSAffineTransform()
            transform.translateX(by: width * (1 - scale) + 12 * (1 - progress), yBy: y * (1 - scale))
            transform.scale(by: scale); transform.concat()
            NSGraphicsContext.current?.cgContext.setAlpha(progress)
            let body = bodyHeight(bubble.1, width: width, index: index)
            let frame = NSRect(x: 0.5, y: y + 0.5, width: width - 1, height: body + 37)
            let shape = NSBezierPath(roundedRect: frame, xRadius: 14, yRadius: 14)
            NSColor(calibratedWhite: index == 0 ? 0.15 : 0.095, alpha: 1).setFill(); shape.fill()
            NSColor(calibratedWhite: 0.3, alpha: 1).setStroke(); shape.lineWidth = 1; shape.stroke()
            (bubble.0 as NSString).draw(at: NSPoint(x: 14, y: y + 9), withAttributes: [
                .font: NSFont.systemFont(ofSize: 10, weight: .semibold),
                .foregroundColor: NSColor(calibratedWhite: 0.72, alpha: 1)])
            let layout = bodyLayout(bubble.1, width: width, index: index)
            NSBezierPath(rect: NSRect(x: 14, y: y + 25, width: width - 28, height: body)).addClip()
            (bubble.1 as NSString).draw(with: NSRect(x: 14, y: y + 25 - layout.offset, width: width - 28, height: layout.height),
                options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine], attributes: bodyStyle)
            NSGraphicsContext.restoreGraphicsState()
            y += body + 46
        }
    }
}

private final class DesktopController {
    let gate: ActionGate
    let worker: DesktopWorker
    weak var window: NSWindow?
    var collapsed = true
    var hidden = false
    var captionPanel: NSPanel?
    var captionView: CompactCaptionsView?
    var captionPayload: [String: String] = [:]
    var captionDeadline: TimeInterval?
    var captionsDismissed = false
    private var captionTimer: Timer?
    var previewPanel: NSPanel?
    var previewImage: NSImage?
    var previewLabel = "Waiting for a desktop capture…"
    var previewVisible = false
    var displayObserver: NSObjectProtocol?
    private var layoutTimer: Timer?
    private var layoutGeneration = 0
    init() {
        gate = ActionGate(logURL: Bundle.main.bundleURL.deletingLastPathComponent()
            .appendingPathComponent("validation/session-\(UUID().uuidString).jsonl"))
        worker = DesktopWorker(gate: gate)
        displayObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.positionWindow() }
    }
    func positionWindow(animated: Bool = false) {
        layoutGeneration += 1
        let generation = layoutGeneration
        layoutTimer?.invalidate(); layoutTimer = nil
        guard let window = window, let screen = window.screen ?? NSScreen.main else { return }
        let visible = hidden ? screen.visibleFrame : screen.visibleFrame.insetBy(dx: 12, dy: 12)
        let width = min(CGFloat(hidden ? 28 : (collapsed ? 72 : 360)), visible.width)
        let height = min(CGFloat(hidden ? 88 : (collapsed ? 292 : 620)), visible.height)
        let frame = NSRect(x: visible.maxX - width, y: visible.midY - height / 2, width: width, height: height)
        // Resize callbacks enter GPUI. Defer every frame until its entity update
        // has released the window; newer requests invalidate both queued work and timers.
        DispatchQueue.main.async { [weak self, weak window] in
            guard let self = self, let window = window, self.layoutGeneration == generation else { return }
            window.level = .floating
            window.isMovable = false
            window.collectionBehavior = [.fullScreenAuxiliary, .canJoinAllSpaces]
            if let panel = window as? NSPanel { panel.hidesOnDeactivate = false }
            let place = { [weak self, weak window] (next: NSRect) in
                guard let self = self, let window = window else { return }
                if window.frame != next { window.setFrame(next, display: true) }
                // GPUI can restore AppKit buttons during a resize. Keep them hidden
                // throughout the transition, including its exact final frame.
                for kind: NSWindow.ButtonType in [.closeButton, .miniaturizeButton, .zoomButton] {
                    let button = window.standardWindowButton(kind)
                    button?.isHidden = true; button?.isEnabled = false
                }
                self.positionPreview(); self.positionCaptions()
            }
            window.orderFront(nil)
            guard animated, window.frame != frame,
                  !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else {
                place(frame); return
            }
            let from = window.frame
            let started = ProcessInfo.processInfo.systemUptime
            let advance = { [weak self] in
                guard let self = self, self.layoutGeneration == generation else { return }
                let progress = CaptionEntrance.progress(
                    elapsed: ProcessInfo.processInfo.systemUptime - started,
                    reducedMotion: NSWorkspace.shared.accessibilityDisplayShouldReduceMotion)
                if progress >= 1 {
                    place(frame)
                    self.layoutTimer?.invalidate(); self.layoutTimer = nil
                } else {
                    place(NSRect(
                        x: from.minX + (frame.minX - from.minX) * progress,
                        y: from.minY + (frame.minY - from.minY) * progress,
                        width: from.width + (frame.width - from.width) * progress,
                        height: from.height + (frame.height - from.height) * progress))
                }
            }
            let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { _ in advance() }
            self.layoutTimer = timer
            RunLoop.main.add(timer, forMode: .common)
            advance()
        }
    }
    func updateCaptions(_ payload: [String: String]) {
        guard captionPayload != payload else { return }
        captionTimer?.invalidate(); captionTimer = nil
        captionPayload = payload
        captionsDismissed = false
        captionDeadline = payload["phase"] == "complete" ? ProcessInfo.processInfo.systemUptime + 5 : nil
        if captionDeadline != nil {
            let timer = Timer(timeInterval: 5, repeats: false) { [weak self] _ in
                self?.dismissExpiredCaptions()
            }
            captionTimer = timer
            RunLoop.main.add(timer, forMode: .common)
        }
        positionCaptions()
    }
    func dismissExpiredCaptions(now: TimeInterval = ProcessInfo.processInfo.systemUptime) {
        guard let deadline = captionDeadline, now >= deadline else { return }
        captionsDismissed = true
        captionTimer?.invalidate(); captionTimer = nil
        positionCaptions()
    }
    func positionCaptions() {
        guard !captionsDismissed, !hidden, collapsed, !previewVisible, let window = window, let screen = window.screen ?? NSScreen.main else {
            captionView?.stopAnimation(); captionPanel?.orderOut(nil); return
        }
        let user = captionPayload["user"] ?? "", agent = captionPayload["agent"] ?? ""
        var bubbles: [(String, String)] = []
        if !user.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { bubbles.append(("You", user)) }
        if !agent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            bubbles.append((captionPayload["agentTitle"] ?? "Codex", agent))
        }
        guard !bubbles.isEmpty else { captionView?.stopAnimation(); captionPanel?.orderOut(nil); return }
        let visible = screen.visibleFrame.insetBy(dx: 12, dy: 12)
        let width = min(CGFloat(264), window.frame.minX - visible.minX - 12)
        guard width >= 160 else { captionView?.stopAnimation(); captionPanel?.orderOut(nil); return }
        if captionPanel == nil {
            let panel = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                                backing: .buffered, defer: false)
            panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = true
            panel.ignoresMouseEvents = true; panel.hidesOnDeactivate = false; panel.level = .floating
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            let view = CompactCaptionsView(frame: .zero)
            view.setAccessibilityElement(true)
            view.setAccessibilityRole(.staticText)
            panel.contentView = view
            captionPanel = panel; captionView = view
        }
        guard let view = captionView, let panel = captionPanel else { return }
        view.update(bubbles, request: captionPayload["request"] ?? "", appearing: !panel.isVisible)
        view.setAccessibilityLabel(bubbles.map { "\($0.0): \($0.1)" }.joined(separator: "\n"))
        let height = min(view.height(width: width), visible.height)
        let top = min(visible.maxY, window.frame.maxY)
        panel.setFrame(NSRect(x: window.frame.minX - width - 12,
                              y: max(visible.minY, top - height), width: width + 16, height: height), display: true)
        view.needsDisplay = true
        panel.orderFrontRegardless()
    }
    func positionPreview() {
        guard !hidden, previewVisible, collapsed, let window = window,
              let screen = window.screen ?? NSScreen.main else { previewPanel?.orderOut(nil); return }
        let visible = screen.visibleFrame.insetBy(dx: 12, dy: 12)
        let width = min(CGFloat(320), window.frame.minX - visible.minX - 12)
        guard width >= 160 else { previewPanel?.orderOut(nil); return }
        if previewPanel == nil {
            let panel = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                                backing: .buffered, defer: false)
            panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = true
            panel.ignoresMouseEvents = true; panel.hidesOnDeactivate = false; panel.level = .floating
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            previewPanel = panel
        }
        guard let panel = previewPanel else { return }
        let height = width * 0.625 + 48
        let view = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        view.wantsLayer = true; view.layer?.backgroundColor = NSColor(calibratedWhite: 0.095, alpha: 1).cgColor
        view.layer?.cornerRadius = 14; view.layer?.masksToBounds = true
        let label = NSTextField(labelWithString: previewLabel)
        label.font = .systemFont(ofSize: 11); label.textColor = .lightGray
        label.lineBreakMode = .byTruncatingTail
        label.frame = NSRect(x: 12, y: height - 34, width: width - 24, height: 20)
        view.addSubview(label)
        let image = NSImageView(frame: NSRect(x: 8, y: 8, width: width - 16, height: height - 48))
        image.image = previewImage; image.imageScaling = .scaleProportionallyUpOrDown
        image.setAccessibilityLabel("Latest captured screen. Snapshot, not a live stream.")
        view.addSubview(image)
        panel.contentView = view
        panel.setFrame(NSRect(x: window.frame.minX - width - 12,
                              y: max(visible.minY, min(visible.maxY - height, window.frame.maxY - height)),
                              width: width, height: height), display: true)
        panel.orderFrontRegardless()
    }
    func control(_ request: [String: Any]) -> [String: Any] {
        switch request["operation"] as? String {
        case "snapshot":
            guard Thread.isMainThread else { return ["success": false] }
            let observation = request["observation"] as? [String: Any] ?? [:]
            previewImage = (observation["screenshot"] as? String).flatMap { url in
                url.split(separator: ",", maxSplits: 1).last.flatMap { Data(base64Encoded: String($0)) }
            }.flatMap { NSImage(data: $0) }
            let date = Date(timeIntervalSince1970: observation["observedAt"] as? Double ?? 0)
            let time = DateFormatter.localizedString(from: date, dateStyle: .none, timeStyle: .medium)
            previewLabel = "Snapshot · \(observation["app"] as? String ?? "Desktop") · \(time)"
            positionPreview()
            return ["success": true]
        case "preview":
            guard Thread.isMainThread else { return ["success": false] }
            previewVisible = request["visible"] as? Bool ?? false
            positionPreview(); positionCaptions()
            return ["success": true]
        case "stop", "pause":
            let timing = gate.pause(sticky: request["operation"] as? String == "stop")
            worker.cursor.hide()
            return ["success": true, "epoch": gate.snapshot().0, "timing": timing]
        case "resume":
            let epoch = request["epoch"] as? Int ?? gate.snapshot().0
            return ["success": gate.resume(epoch: epoch, explicit: request["explicit"] as? Bool ?? false)]
        case "share":
            let enabled = request["enabled"] as? Bool ?? false
            if enabled {
                guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
                    return ["success": false, "error": "Allow Screen Recording for Voice App in System Settings, then retry sharing."]
                }
                guard AXIsProcessTrusted() else {
                    return ["success": false, "error": "Allow Accessibility for Voice App, then retry sharing."]
                }
            } else {
                gate.pause(sticky: true); worker.cursor.hide()
                previewVisible = false; previewImage = nil; previewLabel = "Waiting for a desktop capture…"
                previewPanel?.orderOut(nil)
            }
            worker.setSharing(enabled)
            return ["success": true]
        case "allowAccessibility":
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            return ["success": AXIsProcessTrustedWithOptions(options)]
        case "shutdown":
            DispatchQueue.main.async { self.updateCaptions([:]); self.previewPanel?.orderOut(nil) }
            gate.pause(sticky: true); worker.cursor.hide(); worker.setSharing(false); gate.flushAudit()
            return ["success": true]
        default: return ["success": false, "error": "Unsupported local control operation."]
        }
    }
}

private let controller = DesktopController()
private func parse(_ raw: UnsafePointer<CChar>?) -> [String: Any] {
    guard let raw = raw, let data = String(cString: raw).data(using: .utf8),
          let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
    return value
}
private func json(_ value: [String: Any]) -> String {
    guard let data = try? JSONSerialization.data(withJSONObject: value), let text = String(data: data, encoding: .utf8) else {
        return "{\"success\":false,\"error\":\"Local response encoding failed\"}"
    }
    return text
}

@_cdecl("voice_desktop_control")
public func voiceDesktopControl(_ raw: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>? {
    strdup(json(controller.control(parse(raw))))
}

@_cdecl("voice_desktop_free")
public func voiceDesktopFree(_ pointer: UnsafeMutablePointer<CChar>?) { free(pointer) }

public typealias DesktopReply = @convention(c) (UnsafePointer<CChar>?, UnsafeMutableRawPointer?) -> Void
@_cdecl("voice_desktop_request")
public func voiceDesktopRequest(_ raw: UnsafePointer<CChar>?, _ reply: DesktopReply?, _ context: UnsafeMutableRawPointer?) {
    guard let reply = reply else { return }
    let request = parse(raw)
    if request["operation"] as? String == "microphone" {
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            json(["success": granted, "error": granted ? "" : "Allow Microphone for Voice App in System Settings."])
                .withCString { reply($0, context) }
        }
    } else {
        controller.worker.perform(request) { result in json(result).withCString { reply($0, context) } }
    }
}

@_cdecl("voice_sidebar_layout")
public func voiceSidebarLayout(_ rawView: UnsafeMutableRawPointer?, _ collapsed: Bool, _ hidden: Bool) {
    guard Thread.isMainThread, let rawView = rawView else { return }
    let view = Unmanaged<NSView>.fromOpaque(rawView).takeUnretainedValue()
    controller.window = view.window
    let changed = controller.collapsed != collapsed || controller.hidden != hidden
    controller.collapsed = collapsed
    controller.hidden = hidden
    if !collapsed || hidden { controller.previewVisible = false; controller.previewPanel?.orderOut(nil) }
    controller.positionWindow(animated: changed)
}

@_cdecl("voice_sidebar_captions")
public func voiceSidebarCaptions(_ raw: UnsafePointer<CChar>?) {
    guard Thread.isMainThread else { return }
    controller.updateCaptions(parse(raw).compactMapValues { ($0 as? String).map { String($0.prefix(4000)) } })
}
