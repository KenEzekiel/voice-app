@preconcurrency import AVFoundation
import Speech
import AppKit

// Recognition is on device. No audio files, network clients, or asset downloads.
struct SpeechCaptionBuffer {
    struct Segment {
        var start: Double
        var end: Double
        var text: String
        var final: Bool
    }
    private(set) var segments: [Segment] = []
    private(set) var consumedThrough: Double = -1
    var text: String { segments.sorted { $0.start < $1.start }.map(\.text).joined() }
    var finalized: Bool { !segments.isEmpty && segments.allSatisfy(\.final) }
    mutating func update(start: Double, end: Double, text: String, final: Bool) {
        guard start.isFinite, end.isFinite, end > start, start >= consumedThrough - 0.0001 else { return }
        segments.removeAll { $0.start < end && $0.end > start }
        segments.append(Segment(start: start, end: end, text: text, final: final))
    }
    mutating func consume() {
        consumedThrough = max(consumedThrough, segments.map(\.end).max() ?? consumedThrough)
        segments.removeAll()
    }
}

// Acoustic activity gates sending; it is not a semantic end-of-sentence model.
struct SpeechActivityClock {
    private(set) var speaking = false
    private(set) var speechCount = 0
    private(set) var lastVoiceAt: TimeInterval?
    private var levels: [Double] = Array(repeating: 0.0002, count: 50)
    private var onset = 0
    mutating func update(level: Double, now: TimeInterval) {
        levels.append(level)
        if levels.count > 50 { levels.removeFirst() }
        // A rolling low percentile follows steady fan/microphone noise while
        // retaining the louder syllables. Caption packet timing is not used.
        let floor = levels.sorted()[levels.count / 5]
        // Only suppress near-zero quantization; microphone gain varies widely.
        let threshold = max(0.0001, floor * 3)
        if level > threshold {
            onset += 1
            if onset >= 2 {
                if !speaking { speechCount += 1 }
                speaking = true; lastVoiceAt = now
            }
        } else {
            onset = 0
            if let last = lastVoiceAt, now - last >= 0.12 { speaking = false }
        }
    }
    func quietSeconds(now: TimeInterval) -> Double { lastVoiceAt.map { max(0, now - $0) } ?? 0 }
    static func level(_ buffer: AVAudioPCMBuffer) -> Double {
        let count = Int(buffer.frameLength)
        guard count > 0 else { return 0 }
        var sum = 0.0
        if let samples = buffer.floatChannelData?[0] {
            for index in 0..<count { let value = Double(samples[index]); sum += value * value }
        } else if let samples = buffer.int16ChannelData?[0] {
            for index in 0..<count { let value = Double(samples[index]) / 32768; sum += value * value }
        }
        return sqrt(sum / Double(count))
    }
}

@available(macOS 26.0, *)
@MainActor final class LocalSpeechEngine {
    static let shared = LocalSpeechEngine()
    private var engine: AVAudioEngine?
    private var analyzer: SpeechAnalyzer?
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var transcripts: Task<Void, Never>?
    private var finalizer: Task<Void, Never>?
    private var fixtureFeed: Task<Void, Never>?
    private var generation = 0
    private var caption = SpeechCaptionBuffer()
    private var captionVersion = 0
    private var clock = SpeechActivityClock()
    private var finalizedSpeechCount = 0
    private var changedAt = ProcessInfo.processInfo.systemUptime
    private var lastAudioTime = CMTime.zero
    private var lastFrameAt = ProcessInfo.processInfo.systemUptime
    private var failure: String?
    private var active = false
    private var muted = true
    private let synthesizer = AVSpeechSynthesizer()

    private func fail(_ message: String, generation: Int) {
        guard generation == self.generation else { return }
        failure = message
        engine?.stop()
        active = false
    }
    func stop() async {
        generation += 1
        active = false; muted = true
        engine?.stop(); engine?.inputNode.removeTap(onBus: 0); engine = nil
        continuation?.finish(); continuation = nil
        transcripts?.cancel(); finalizer?.cancel(); fixtureFeed?.cancel()
        transcripts = nil; finalizer = nil; fixtureFeed = nil
        let previous = analyzer; analyzer = nil
        caption = SpeechCaptionBuffer(); captionVersion += 1
        clock = SpeechActivityClock(); finalizedSpeechCount = 0; lastAudioTime = .zero
        synthesizer.stopSpeaking(at: .immediate)
        await previous?.cancelAndFinishNow()
    }
    // inputFile is used only by the file-replay test; the app request API cannot supply it.
    func start(muted: Bool, inputFile: AVAudioFile? = nil) async throws {
        let ticket = generation + 1
        await stop()
        guard generation == ticket else { throw LocalSpeechError.superseded }
        failure = nil
        guard SpeechTranscriber.isAvailable,
              await SpeechTranscriber.installedLocales.contains(where: { $0.identifier == "en_US" }) else {
            throw LocalSpeechError.unavailable
        }
        guard generation == ticket else { throw LocalSpeechError.superseded }
        self.muted = muted
        if muted { active = true; return }
        guard inputFile != nil || AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            throw LocalSpeechError.microphone
        }
        let transcriber = SpeechTranscriber(locale: Locale(identifier: "en_US"),
            transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults], attributeOptions: [])
        let modules: [any SpeechModule] = [transcriber]
        let engine = inputFile == nil ? AVAudioEngine() : nil
        let inputFormat = inputFile?.processingFormat ?? engine!.inputNode.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0,
              let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: modules, considering: inputFormat),
              let converter = AVAudioConverter(from: inputFormat, to: format) else { throw LocalSpeechError.format }
        guard generation == ticket else { throw LocalSpeechError.superseded }
        let analyzer = SpeechAnalyzer(modules: modules, options: .init(priority: .userInitiated, modelRetention: .whileInUse))
        self.analyzer = analyzer; self.engine = engine
        try await analyzer.prepareToAnalyze(in: format)
        guard generation == ticket else { throw LocalSpeechError.superseded }
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .bufferingNewest(32))
        self.continuation = continuation
        transcripts = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self = self, self.generation == ticket else { return }
                    self.caption.update(start: result.range.start.seconds, end: CMTimeRangeGetEnd(result.range).seconds,
                                        text: String(result.text.characters), final: result.isFinal)
                    self.captionVersion += 1; self.changedAt = ProcessInfo.processInfo.systemUptime
                }
            } catch { self?.fail("Local transcription stopped. Restart session to retry.", generation: ticket) }
        }
        try await analyzer.start(inputSequence: stream)
        guard generation == ticket else { throw LocalSpeechError.superseded }
        var frameOffset: Int64 = 0
        let feed: (AVAudioPCMBuffer) -> Void = { buffer in
            let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * format.sampleRate / inputFormat.sampleRate)) + 16
            guard let converted = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return }
            var supplied = false
            var error: NSError?
            converter.convert(to: converted, error: &error) { _, status in
                if supplied { status.pointee = .noDataNow; return nil }
                supplied = true; status.pointee = .haveData; return buffer
            }
            if error != nil {
                continuation.finish()
                Task { @MainActor [weak self] in self?.fail("Microphone audio conversion failed. Restart session to retry.", generation: ticket) }
                return
            }
            if converted.frameLength > 0 {
                let start = CMTime(value: frameOffset, timescale: Int32(format.sampleRate))
                frameOffset += Int64(converted.frameLength)
                let end = CMTime(value: frameOffset, timescale: Int32(format.sampleRate))
                let level = SpeechActivityClock.level(converted)
                Task { @MainActor [weak self] in
                    guard let self = self, self.generation == ticket else { return }
                    self.clock.update(level: level, now: ProcessInfo.processInfo.systemUptime)
                    self.lastAudioTime = end
                    self.lastFrameAt = ProcessInfo.processInfo.systemUptime
                }
                if case .dropped = continuation.yield(AnalyzerInput(buffer: converted, bufferStartTime: start)) {
                    continuation.finish()
                    Task { @MainActor [weak self] in self?.fail("Microphone audio fell behind. Request was not sent; restart session to retry.", generation: ticket) }
                }
            }
        }
        if let inputFile = inputFile {
            fixtureFeed = Task.detached {
                let silenceFrames = AVAudioFrameCount(inputFormat.sampleRate * 2)
                var silent: AVAudioFrameCount = 0
                do {
                    while !Task.isCancelled {
                        guard let buffer = AVAudioPCMBuffer(pcmFormat: inputFormat, frameCapacity: 1024) else { break }
                        if inputFile.framePosition < inputFile.length {
                            try inputFile.read(into: buffer)
                        } else if silent < silenceFrames {
                            buffer.frameLength = min(1024, silenceFrames - silent)
                            for item in UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList) {
                                if let data = item.mData { memset(data, 0, Int(item.mDataByteSize)) }
                            }
                            silent += buffer.frameLength
                        } else { break }
                        feed(buffer)
                        try await Task.sleep(nanoseconds: UInt64(Double(buffer.frameLength) / inputFormat.sampleRate * 1_000_000_000))
                    }
                } catch { continuation.finish() }
            }
        } else if let engine = engine {
            engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { buffer, _ in feed(buffer) }
            engine.prepare()
            try engine.start()
        }
        lastFrameAt = ProcessInfo.processInfo.systemUptime
        active = true
    }
    func snapshot() -> [String: Any] {
        if active && !muted && ProcessInfo.processInfo.systemUptime - lastFrameAt > 1.5 {
            fail("Microphone stopped delivering audio. Restart session to retry.", generation: generation)
        }
        let quiet = clock.quietSeconds(now: ProcessInfo.processInfo.systemUptime)
        if active && !muted && !clock.speaking && quiet >= 0.65 && clock.speechCount > finalizedSpeechCount && finalizer == nil,
           let analyzer = analyzer {
            let ticket = generation, count = clock.speechCount, through = lastAudioTime
            finalizer = Task { [weak self] in
                do {
                    try await analyzer.finalize(through: through)
                    guard let self = self, self.generation == ticket else { return }
                    self.finalizedSpeechCount = count; self.finalizer = nil
                } catch { self?.fail("Local speech could not finalize this request. Restart session to retry.", generation: ticket) }
            }
        }
        return ["success": failure == nil && active, "error": failure ?? "", "backend": "apple",
            "muted": muted, "speaking": clock.speaking, "speechCount": clock.speechCount, "quietSeconds": quiet,
            "text": caption.text, "version": captionVersion,
            "finalized": caption.finalized, "finalizedSpeechCount": finalizedSpeechCount,
            "ready": active && !muted && !clock.speaking && quiet >= 0.9 && caption.finalized
                && finalizedSpeechCount == clock.speechCount && ProcessInfo.processInfo.systemUptime - changedAt >= 0.15]
    }
    func request(_ request: [String: Any]) async -> [String: Any] {
        do {
            switch request["action"] as? String {
            case "start": try await start(muted: request["muted"] as? Bool ?? false)
            case "controls":
                let nextMuted = request["muted"] as? Bool ?? true
                if nextMuted != muted || !active { try await start(muted: nextMuted) }
                if request["suppressed"] as? Bool != false { synthesizer.stopSpeaking(at: .immediate) }
            case "stop": await stop(); return ["success": true]
            case "consume":
                guard (request["manual"] as? Bool == true || snapshot()["ready"] as? Bool == true),
                      request["version"] as? Int == captionVersion else { return ["success": false, "error": "Speech changed before dispatch."] }
                caption.consume(); captionVersion += 1
            case "speak":
                guard muted else { return ["success": false, "error": "Mute microphone before playback."] }
                synthesizer.speak(AVSpeechUtterance(string: String((request["text"] as? String ?? "").prefix(2000))))
            case "status": break
            default: return ["success": false, "error": "Unsupported local speech operation."]
            }
            return snapshot()
        } catch {
            if let local = error as? LocalSpeechError, case .superseded = local {
                return ["success": false, "error": local.message]
            }
            await stop()
            return ["success": false, "error": (error as? LocalSpeechError)?.message ?? "Apple speech could not start. No cloud speech fallback was used."]
        }
    }
}

enum LocalSpeechError: Error {
    case unavailable, microphone, format, superseded
    var message: String {
        switch self {
        case .superseded: return "Local speech start was superseded by a newer control."
        case .unavailable: return "Apple on-device English speech assets are unavailable. No download or cloud speech fallback was used."
        case .microphone: return "Allow Microphone for Voice App in System Settings."
        case .format: return "The selected microphone has no supported local speech audio format."
        }
    }
}
