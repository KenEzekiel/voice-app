import AppKit

struct AgentCursorPath {
    static let scale: CGFloat = 0.75
    static let duration = 0.10
    static func point(from: CGPoint, to: CGPoint, progress: Double) -> CGPoint {
        let t = max(0, min(1, progress))
        let eased = t * t * (3 - 2 * t)
        return CGPoint(x: from.x + (to.x - from.x) * eased,
                       y: from.y + (to.y - from.y) * eased)
    }
}

private final class AgentCursorView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        let graphite = NSColor(srgbRed: 48.0 / 255, green: 52.0 / 255, blue: 59.0 / 255, alpha: 1)
        let ivory = NSColor(srgbRed: 247.0 / 255, green: 245.0 / 255, blue: 239.0 / 255, alpha: 1)
        let outline = NSColor(srgbRed: 17.0 / 255, green: 19.0 / 255, blue: 24.0 / 255, alpha: 1)
        let arrow = NSBezierPath()
        // The selected compact silhouette, translated to the existing hotspot.
        arrow.move(to: NSPoint(x: 10, y: 48))
        for point in [NSPoint(x: 10, y: 26), NSPoint(x: 17, y: 32),
                      NSPoint(x: 21, y: 24), NSPoint(x: 25, y: 26),
                      NSPoint(x: 21, y: 34), NSPoint(x: 32, y: 34)] {
            arrow.line(to: point)
        }
        arrow.close()
        arrow.transform(using: AffineTransform(scale: AgentCursorPath.scale))
        arrow.lineJoinStyle = .round
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.2)
        shadow.shadowBlurRadius = 2
        shadow.shadowOffset = NSSize(width: 0, height: -1)
        shadow.set()
        outline.setStroke(); arrow.lineWidth = 1.8; arrow.stroke()
        NSGraphicsContext.restoreGraphicsState()
        ivory.setStroke(); arrow.lineWidth = 1.1; arrow.stroke()
        graphite.setFill(); arrow.fill()
    }
}

/// A visual pointer only. It never moves the user's pointer or posts input.
/// All AppKit work stays on the main queue; the worker waits outside ActionGate.
final class AgentCursor {
    private var panel: NSPanel?
    private var position: CGPoint?
    private var timer: Timer?
    private var finish: ((Bool) -> Void)?

    func hide() {
        DispatchQueue.main.async {
            self.timer?.invalidate(); self.timer = nil
            self.panel?.orderOut(nil)
            self.finish?(false); self.finish = nil
        }
    }

    func move(to target: CGPoint, gate: ActionGate, epoch: Int, waitForArrival: Bool = true) throws {
        let done = DispatchSemaphore(value: 0)
        var arrived = false
        DispatchQueue.main.async {
            let current = gate.snapshot()
            guard current.0 == epoch, current.1 == .working else { done.signal(); return }
            if self.panel == nil {
                let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 33, height: 45),
                                    styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
                panel.isOpaque = false; panel.backgroundColor = .clear
                panel.hasShadow = false; panel.ignoresMouseEvents = true
                panel.hidesOnDeactivate = false
                panel.level = .floating
                panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
                panel.contentView = AgentCursorView(frame: NSRect(x: 0, y: 0, width: 33, height: 45))
                self.panel = panel
            }
            let origin = self.position ?? CGEvent(source: nil)?.location ?? target
            let start = ProcessInfo.processInfo.systemUptime
            let duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0.0 : AgentCursorPath.duration
            self.finish = { success in arrived = success; done.signal() }
            let update = {
                let state = gate.snapshot()
                guard state.0 == epoch, state.1 == .working else {
                    self.hide(); return
                }
                let progress = duration == 0 ? 1 : min(1, (ProcessInfo.processInfo.systemUptime - start) / duration)
                let point = AgentCursorPath.point(from: origin, to: target, progress: progress)
                self.position = point
                let primaryHeight = CGDisplayBounds(CGMainDisplayID()).height
                self.panel?.setFrameOrigin(NSPoint(x: point.x - 7.5, y: primaryHeight - point.y - 36))
                self.panel?.orderFrontRegardless()
                if progress >= 1 {
                    self.timer?.invalidate(); self.timer = nil
                    self.finish?(true); self.finish = nil
                }
            }
            if duration > 0 {
                self.timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) { _ in update() }
                if let timer = self.timer { RunLoop.main.add(timer, forMode: .common) }
            }
            update()
        }
        guard waitForArrival else { return }
        guard done.wait(timeout: .now() + 1) == .success, arrived else {
            hide()
            throw GateError.invalidated
        }
    }
}
