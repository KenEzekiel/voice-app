import AVFoundation
import Foundation

@main struct LocalSpeechReplayTests {
    @MainActor static func main() async throws {
        guard #available(macOS 26.0, *) else { fatalError("Requires macOS 26") }
        let speech = LocalSpeechEngine.shared
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: CommandLine.arguments[1]))
        let duration = Double(file.length) / file.processingFormat.sampleRate
        let expected = CommandLine.arguments[2].split(separator: "|").map { $0.lowercased().filter { $0.isLetter || $0.isNumber } }
        let began = ProcessInfo.processInfo.systemUptime
        try await speech.start(muted: false, inputFile: file)
        let prepared = ProcessInfo.processInfo.systemUptime
        var firstCaption: Double?
        var heard: [String] = []
        var readyTimes: [Double] = []
        var speechCount = 0
        var recognizedBeforeSend = false
        var lastVersion: Int?
        var states: [[String: Any]] = []
        while ProcessInfo.processInfo.systemUptime - prepared < duration + 10 {
            let status = speech.snapshot()
            if status["version"] as? Int != lastVersion || status["success"] as? Bool != true {
                lastVersion = status["version"] as? Int
                states.append(status.filter { ["success", "error", "version", "speaking", "speechCount", "quietSeconds", "ready"].contains($0.key) })
            }
            if heard.count < expected.count {
                let recognized = (status["text"] as? String ?? "").lowercased().filter { $0.isLetter || $0.isNumber }
                recognizedBeforeSend = recognizedBeforeSend || recognized == expected[heard.count]
            }
            if status["success"] as? Bool != true { break }
            if !(status["text"] as? String ?? "").isEmpty && firstCaption == nil {
                firstCaption = ProcessInfo.processInfo.systemUptime - prepared
            }
            if status["ready"] as? Bool == true {
                heard.append((status["text"] as? String ?? "").lowercased().filter { $0.isLetter || $0.isNumber })
                readyTimes.append(ProcessInfo.processInfo.systemUptime - prepared)
                speechCount = status["speechCount"] as? Int ?? 0
                let consumed = await speech.request(["action": "consume", "version": status["version"]!, "manual": false])
                precondition(consumed["success"] as? Bool == true)
                precondition((speech.snapshot()["text"] as? String ?? "").isEmpty)
                if heard.count == expected.count { break }
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let completed = ProcessInfo.processInfo.systemUptime
        let result: [String: Any] = ["passed": heard == expected, "requests": heard.count, "requestReadySeconds": readyTimes, "states": states,
            "captionMatchedExpectedBeforeSend": recognizedBeforeSend,
            "setupMs": (prepared - began) * 1000,
            "audioSeconds": duration,
            "firstCaptionSeconds": firstCaption ?? -1,
            "readyAfterFileEndSeconds": completed - prepared - duration,
            "speechCount": speechCount,
            "proof": "Production Apple transcription, conversion, acoustic clock and finalization; generated file paced in real time. No microphone or desktop input."]
        await speech.stop()
        let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data + Data([10]))
        if result["passed"] as? Bool != true { exit(1) }
    }
}
