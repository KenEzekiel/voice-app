@preconcurrency import AVFoundation
import Speech

// Local, file-only streaming prototype. Never opens a microphone or downloads assets.
private final class SpeechProbeLog: @unchecked Sendable {
    private let lock = NSLock()
    private var rows: [[String: Any]] = []
    private var finalText = ""
    let started = ProcessInfo.processInfo.systemUptime
    func record(_ event: String, range: CMTimeRange, speech: Bool? = nil, final: Bool? = nil, text: String = "") {
        lock.lock(); defer { lock.unlock() }
        var row: [String: Any] = ["event": event,
            "elapsedMs": (ProcessInfo.processInfo.systemUptime - started) * 1000,
            "audioStartSeconds": range.start.seconds, "audioEndSeconds": CMTimeRangeGetEnd(range).seconds]
        if let speech = speech { row["speechDetected"] = speech }
        if let final = final { row["final"] = final; if final { finalText += text } }
        rows.append(row)
    }
    func report(expected: String, audioSeconds: Double, setupMs: Double) throws {
        lock.lock(); defer { lock.unlock() }
        let normalized = finalText.lowercased().filter { $0.isLetter || $0.isNumber }
        let expected = expected.lowercased().filter { $0.isLetter || $0.isNumber }
        let result: [String: Any] = ["proof": "Real Apple on-device recognition; file buffers paced in real time. No mic, network model or GUI actions.",
            "audioSeconds": audioSeconds, "setupMs": setupMs, "finalMatchesExpected": normalized == expected,
            "speechActivityAvailable": rows.contains { $0["event"] as? String == "audioActivity" },
            "events": rows]
        let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data + Data([10]))
        if normalized != expected { throw NSError(domain: "SpeechProbe", code: 2, userInfo: [NSLocalizedDescriptionKey: "Final recognition did not match the expected fixture."]) }
    }
}

@main struct LocalSpeechProbe {
    static func main() async {
        do {
            guard CommandLine.arguments.count == 3 else {
                throw NSError(domain: "SpeechProbe", code: 1, userInfo: [NSLocalizedDescriptionKey: "Usage: local-speech-probe AUDIO_FILE EXPECTED_TEXT"])
            }
            if #available(macOS 26.0, *) {
                try await run(url: URL(fileURLWithPath: CommandLine.arguments[1]), expected: CommandLine.arguments[2])
            } else { throw NSError(domain: "SpeechProbe", code: 1, userInfo: [NSLocalizedDescriptionKey: "Requires macOS 26 or later."]) }
        } catch {
            FileHandle.standardError.write(Data(("Local speech probe failed: \(error.localizedDescription)\n").utf8))
            exit(1)
        }
    }

    @available(macOS 26.0, *)
    static func run(url: URL, expected: String) async throws {
        guard SpeechTranscriber.isAvailable,
              await SpeechTranscriber.installedLocales.contains(where: { $0.identifier == "en_US" }) else {
            throw NSError(domain: "SpeechProbe", code: 1, userInfo: [NSLocalizedDescriptionKey: "English assets unavailable. No download or cloud fallback attempted."])
        }
        let transcriber = SpeechTranscriber(locale: Locale(identifier: "en_US"),
            transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults], attributeOptions: [.audioTimeRange])
        let detector = SpeechDetector(detectionOptions: .init(sensitivityLevel: .medium), reportResults: true)
        let modules: [any SpeechModule] = [transcriber, detector]
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: modules) else {
            throw NSError(domain: "SpeechProbe", code: 1, userInfo: [NSLocalizedDescriptionKey: "No compatible audio format."])
        }
        let file = try AVAudioFile(forReading: url)
        guard file.length > 0, file.length < AVAudioFramePosition(file.processingFormat.sampleRate * 60),
              let source = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length)),
              let converter = AVAudioConverter(from: file.processingFormat, to: format),
              let audio = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(Double(file.length) * format.sampleRate / file.processingFormat.sampleRate) + 1024) else {
            throw NSError(domain: "SpeechProbe", code: 1, userInfo: [NSLocalizedDescriptionKey: "Fixture must be a supported audio file shorter than 60 seconds."])
        }
        try file.read(into: source)
        var supplied = false
        var conversionError: NSError?
        converter.convert(to: audio, error: &conversionError) { _, status in
            if supplied { status.pointee = .endOfStream; return nil }
            supplied = true; status.pointee = .haveData; return source
        }
        if let error = conversionError { throw error }
        let analyzer = SpeechAnalyzer(modules: modules,
            options: .init(priority: .userInitiated, modelRetention: .whileInUse))
        let setupStart = ProcessInfo.processInfo.systemUptime
        try await analyzer.prepareToAnalyze(in: format)
        let setupMs = (ProcessInfo.processInfo.systemUptime - setupStart) * 1000
        let log = SpeechProbeLog()
        let transcripts = Task {
            for try await result in transcriber.results {
                log.record("transcript", range: result.range, final: result.isFinal, text: String(result.text.characters))
            }
        }
        let activity = Task {
            for try await result in detector.results {
                log.record("audioActivity", range: result.range, speech: result.speechDetected)
            }
        }
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        do {
            try await analyzer.start(inputSequence: stream)
            let chunkFrames = AVAudioFrameCount(format.sampleRate / 10)
            let silenceFrames = AVAudioFrameCount(format.sampleRate)
            let totalFrames = audio.frameLength + silenceFrames
            var offset: AVAudioFrameCount = 0
            while offset < totalFrames {
                let count = min(chunkFrames, totalFrames - offset)
                let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count)!
                buffer.frameLength = count
                let destinations = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
                let sources = UnsafeMutableAudioBufferListPointer(audio.mutableAudioBufferList)
                let bytesPerFrame = Int(format.streamDescription.pointee.mBytesPerFrame)
                for index in destinations.indices {
                    let dest = destinations[index].mData!
                    memset(dest, 0, Int(count) * bytesPerFrame)
                    if offset < audio.frameLength {
                        memcpy(dest, sources[index].mData!.advanced(by: Int(offset) * bytesPerFrame),
                            Int(min(count, audio.frameLength - offset)) * bytesPerFrame)
                    }
                }
                try await Task.sleep(nanoseconds: UInt64(Double(count) / format.sampleRate * 1_000_000_000))
                continuation.yield(AnalyzerInput(buffer: buffer,
                    bufferStartTime: CMTime(value: Int64(offset), timescale: Int32(format.sampleRate))))
                offset += count
            }
            continuation.finish()
            try await analyzer.finalizeAndFinishThroughEndOfInput()
            try await transcripts.value
            try await activity.value
            try log.report(expected: expected, audioSeconds: Double(audio.frameLength) / format.sampleRate, setupMs: setupMs)
        } catch {
            continuation.finish()
            await analyzer.cancelAndFinishNow()
            transcripts.cancel(); activity.cancel()
            throw error
        }
    }
}
