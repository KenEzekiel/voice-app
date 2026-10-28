import AppKit

@main struct DesktopWorkerTests {
    static func main() throws {
        var presses = 0, pointerEvents = 0
        let semantic = try deliverClick(press: { presses += 1; return .success }, pointer: { pointerEvents += 1 })
        precondition(semantic == "accessibility" && presses == 1 && pointerEvents == 0)
        for failure: AXError in [.cannotComplete, .actionUnsupported, .invalidUIElement] {
            do {
                _ = try deliverClick(press: { presses += 1; return failure }, pointer: { pointerEvents += 1 })
                preconditionFailure("Unconfirmed press was accepted")
            } catch is WorkerError {}
            precondition(pointerEvents == 0, "Unconfirmed semantic press dispatched a duplicate mouse fallback")
        }
        let physical = try deliverClick(press: nil, pointer: { pointerEvents += 1 })
        precondition(physical == "sharedPointer" && pointerEvents == 1 && presses == 4)
        let stopped = ActionGate()
        let stoppedEpoch = stopped.snapshot().0
        stopped.pause(sticky: true)
        do {
            _ = try stopped.dispatch(epoch: stoppedEpoch) {
                try deliverClick(press: { presses += 1; return .success }, pointer: { pointerEvents += 1 })
            }
            preconditionFailure("Stop admitted a semantic click")
        } catch GateError.invalidated {}
        precondition(presses == 4 && pointerEvents == 1)
        precondition(24 * AgentCursorPath.scale == 18 && AgentCursorPath.duration <= 0.10)
        let from = CGPoint(x: -120, y: 40), to = CGPoint(x: 200, y: -80)
        precondition(AgentCursorPath.point(from: from, to: to, progress: 0) == from)
        precondition(AgentCursorPath.point(from: from, to: to, progress: 1) == to)
        precondition(AgentCursorPath.point(from: from, to: to, progress: 0.5) == CGPoint(x: 40, y: -20))
        var last = from.x
        for frame in 1...60 {
            let point = AgentCursorPath.point(from: from, to: to, progress: Double(frame) / 60)
            precondition(point.x >= last && point.x <= to.x)
            last = point.x
        }
        let main = ScreenshotGeometry(display: CGRect(x: 0, y: 0, width: 3200, height: 2000),
                                      pixels: CGSize(width: 1600, height: 1000))
        let center = try main.point(x: 800, y: 500)
        precondition(center == CGPoint(x: 1600, y: 1000))
        let integerRequest = try JSONSerialization.jsonObject(with: Data("{\"x\":800,\"y\":500}".utf8)) as! [String: Any]
        let parsedPoint = try main.point(request: integerRequest)
        precondition(parsedPoint == center)
        do { _ = try main.point(request: ["element": 4]); preconditionFailure("Missing coordinates accepted") }
        catch let error as WorkerError { precondition(error.message.contains("x and y")) }
        do { _ = try main.point(x: 1800, y: 50); preconditionFailure("Desktop-sized coordinates accepted") }
        catch let error as WorkerError { precondition(error.message.contains("1600") && error.message.contains("1000")) }
        let left = ScreenshotGeometry(display: CGRect(x: -1920, y: -100, width: 1920, height: 1080),
                                      pixels: CGSize(width: 960, height: 540))
        let offset = try left.point(x: 100, y: 100)
        precondition(offset == CGPoint(x: -1720, y: 100))
        for (x, y) in [(-1.0, 0.0), (1600, 0), (0, 1000), (.nan, 1), (1, .infinity)] {
            do { _ = try main.point(x: x, y: y); preconditionFailure("Invalid coordinate accepted") }
            catch is WorkerError {}
        }
        let text = String(repeating: "a", count: 19) + "🙂" + String(repeating: "b", count: 70)
        let chunks = try typingChunks(text)
        precondition(chunks.allSatisfy { $0.count <= 20 })
        precondition(chunks.flatMap { $0 } == Array(text.utf16))
        for chunk in chunks { precondition(String(decoding: chunk, as: UTF16.self).contains("�") == false) }
        do { _ = try typingChunks(String(repeating: "a", count: 501)); preconditionFailure("Oversize typing accepted") }
        catch is WorkerError {}
        let gate = ActionGate()
        let worker = DesktopWorker(gate: gate)
        let done = DispatchSemaphore(value: 0)
        var rejected = false
        worker.perform(["operation": "click", "x": 1, "y": 1, "epoch": 0, "revision": "fake"]) { result in
            rejected = result["success"] as? Bool == false
            done.signal()
        }
        precondition(done.wait(timeout: .now() + 2) == .success && rejected)
        precondition(!gate.audit().contains { $0["event"] as? String == "dispatch" })
        let readback = gate.audit().last { $0["event"] as? String == "actionReadback" }
        precondition(readback?["operation"] as? String == "click")
        precondition(readback?["success"] as? Bool == false)
        precondition((readback?["error"] as? String)?.contains("sharing is off") == true)
        worker.perform(["operation": "launchApp", "bundleID": "com.apple.TextEdit", "epoch": 0, "revision": "fake"]) { result in
            rejected = result["success"] as? Bool == false
            done.signal()
        }
        precondition(done.wait(timeout: .now() + 2) == .success && rejected)
        precondition(!gate.audit().contains { $0["event"] as? String == "dispatch" })
        worker.perform(["operation": "activate", "pid": ProcessInfo.processInfo.processIdentifier, "epoch": 0, "revision": "fake"]) { result in
            rejected = result["success"] as? Bool == false
            done.signal()
        }
        precondition(done.wait(timeout: .now() + 2) == .success && rejected)
        precondition(!gate.audit().contains { $0["event"] as? String == "dispatch" })
        print("Desktop worker: semantic click without pointer events, no fallback after failed press, Stop admission, 18pt cursor/100ms motion, coordinate scaling, Unicode and sharing-off rejection passed. No desktop captured or input posted.")
    }
}
