import Foundation

@main struct GateTests {
    static func main() throws {
        var trials: [[String: Any]] = []
        for trial in 1...10 {
            let gate = ActionGate()
            let epoch = gate.snapshot().0
            precondition(gate.resume(epoch: epoch))
            let began = DispatchSemaphore(value: 0)
            let finished = DispatchSemaphore(value: 0)
            let queue = DispatchQueue(label: "test.desktop")
            let inFlight = trial % 2 == 0
            if inFlight {
                queue.async {
                    try! gate.dispatch(epoch: epoch) {
                        began.signal()
                        Thread.sleep(forTimeInterval: 0.02)
                    }
                    finished.signal()
                }
                began.wait()
            }
            let stop = gate.pause(sticky: true)
            if inFlight { finished.wait() }
            let acknowledged = stop["acknowledged"] as! Double
            // Both stale queued work and a caller with the current epoch fail.
            let current = gate.snapshot().0
            for token in [epoch, current] {
                do {
                    try gate.dispatch(epoch: token) { () }
                    preconditionFailure("Stopped gate accepted dispatch")
                } catch GateError.invalidated {} catch { throw error }
            }
            precondition(!gate.resume(epoch: current), "automatic correction reopened Stop")
            let late = gate.audit().filter { ($0["event"] as? String == "dispatch") && ($0["time"] as! Double > acknowledged) }
            precondition(late.isEmpty)
            trials.append(["trial": trial, "stopDelayMs": stop["delayMs"]!,
                "alreadyDispatchedEffects": inFlight ? 1 : 0,
                "newDispatchesAfterAcknowledgement": late.count,
                "effectsStillInFlightAtAcknowledgement": 0,
                "extraSteering": "none; synthetic controller trial"])
            precondition(gate.resume(epoch: current, explicit: true))
            do { try gate.dispatch(epoch: epoch) {}; preconditionFailure("stale generation accepted after resume") }
            catch GateError.invalidated {}
            try gate.dispatch(epoch: current) {}
            gate.pause()
            precondition(!gate.resume(epoch: current), "stale replan reopened paused gate")
        }
        let data = try JSONSerialization.data(withJSONObject: [
            "proof": "Real local action gate with synthetic dispatched effects; not GUI or voice Stop trials",
            "trials": trials], options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    }
}
