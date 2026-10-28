import Foundation

/// All GUI mutations pass through this lock, including the actual AX call.
/// Stop acknowledgement occurs after any synchronous dispatched mutation settles.
final class ActionGate {
    enum State: String { case paused, working, stopped }
    private let lock = NSLock()
    private var generation = 0
    private var state: State = .paused
    private var sequence = 0
    private var records: [[String: Any]] = []
    private let logQueue = DispatchQueue(label: "voice-app.audit")
    private var logHandle: FileHandle?

    init(logURL: URL? = nil) {
        if let url = logURL {
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if FileManager.default.createFile(atPath: url.path, contents: nil) {
                logHandle = try? FileHandle(forWritingTo: url)
            }
        }
    }
    private func record(_ item: [String: Any]) {
        records.append(item)
        guard let handle = logHandle, let data = try? JSONSerialization.data(withJSONObject: item) else { return }
        logQueue.async { try? handle.write(contentsOf: data + Data([10])) }
    }
    func flushAudit() { logQueue.sync {} }
    func noteReadback(epoch: Int, operation: String, success: Bool, revision: String?, error: String? = nil, inputMethod: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        var item: [String: Any] = ["event": "actionReadback", "epoch": epoch,
                "operation": operation, "success": success,
                "revision": revision ?? "unavailable", "time": ProcessInfo.processInfo.systemUptime]
        if let error = error { item["error"] = error }
        if let method = inputMethod, ["accessibility", "sharedPointer"].contains(method) { item["inputMethod"] = method }
        record(item)
    }

    func snapshot() -> (Int, State) {
        lock.lock(); defer { lock.unlock() }
        return (generation, state)
    }
    @discardableResult func pause(sticky: Bool = false) -> [String: Any] {
        let requested = ProcessInfo.processInfo.systemUptime
        lock.lock(); defer { lock.unlock() }
        generation += 1
        if sticky || state == .stopped { state = .stopped } else { state = .paused }
        let acknowledged = ProcessInfo.processInfo.systemUptime
        let record: [String: Any] = ["event": sticky ? "stopAcknowledged" : "paused",
            "epoch": generation, "requested": requested, "acknowledged": acknowledged,
            "delayMs": (acknowledged - requested) * 1000, "lastDispatch": sequence,
            "inFlightAtAcknowledgement": 0]
        self.record(record)
        return record
    }
    /// Automatic correction recovery must never reopen an explicit Stop.
    func resume(epoch: Int, explicit: Bool = false) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard epoch == generation, explicit || state != .stopped else { return false }
        state = .working
        record(["event": "resumed", "epoch": epoch, "explicit": explicit,
                "time": ProcessInfo.processInfo.systemUptime])
        return true
    }
    func dispatch<T>(epoch: Int, _ action: () throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        guard epoch == generation, state == .working else { throw GateError.invalidated }
        sequence += 1
        record(["event": "dispatch", "epoch": epoch, "sequence": sequence,
                        "time": ProcessInfo.processInfo.systemUptime])
        // Never enqueue a second OS mutation inside this closure.
        do {
            let result = try action()
            record(["event": "actionSettled", "epoch": epoch, "sequence": sequence,
                    "accepted": true, "time": ProcessInfo.processInfo.systemUptime])
            return result
        } catch {
            record(["event": "actionSettled", "epoch": epoch, "sequence": sequence,
                    "accepted": false, "time": ProcessInfo.processInfo.systemUptime])
            throw error
        }
    }
    func audit() -> [[String: Any]] {
        lock.lock(); defer { lock.unlock() }
        return records
    }
}
enum GateError: Error { case invalidated }
