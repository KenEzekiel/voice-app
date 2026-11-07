import Foundation

@main struct LocalSpeechBufferTests {
    static func main() {
        var buffer = SpeechCaptionBuffer()
        buffer.update(start: 0, end: 1, text: "Open the", final: false)
        buffer.update(start: 0, end: 2, text: "Open the browser", final: true)
        buffer.update(start: 2, end: 3, text: " and type hello", final: false)
        precondition(buffer.text == "Open the browser and type hello")
        precondition(!buffer.finalized)
        buffer.update(start: 2, end: 3, text: " and type hello.", final: true)
        precondition(buffer.finalized)
        precondition(buffer.text == "Open the browser and type hello.")
        buffer.consume()
        buffer.update(start: 0, end: 3, text: "Open the browser and type hello.", final: true)
        precondition(buffer.text.isEmpty, "Late results must not replay consumed speech")
        buffer.update(start: 3, end: 4, text: "Close the tab.", final: true)
        precondition(buffer.text == "Close the tab.")
        buffer.update(start: .nan, end: 5, text: "invalid", final: true)
        precondition(buffer.text == "Close the tab.")
        var ordered = SpeechCaptionBuffer()
        ordered.update(start: 1, end: 2, text: " settings", final: true)
        ordered.update(start: 0, end: 1, text: "Open", final: false)
        precondition(ordered.text == "Open settings" && !ordered.finalized)
        ordered.update(start: 0, end: 1, text: "Browser", final: true)
        precondition(ordered.text == "Browser settings" && ordered.finalized)
        ordered.consume()
        precondition(ordered.text.isEmpty && !ordered.finalized)
        var clock = SpeechActivityClock()
        for step in 0..<50 { clock.update(level: 0.0002, now: Double(step) / 50) }
        precondition(clock.speechCount == 0 && !clock.speaking)
        clock.update(level: 0.04, now: 1)
        precondition(clock.speechCount == 0, "One transient must not begin speech")
        clock.update(level: 0.04, now: 1.02)
        precondition(clock.speaking && clock.speechCount == 1)
        clock.update(level: 0.0002, now: 1.08)
        precondition(clock.speaking, "A brief in-word pause must not end speech")
        clock.update(level: 0.0002, now: 1.3)
        precondition(!clock.speaking && clock.quietSeconds(now: 1.95) >= 0.9)
        for gain in [1.0, 0.03] {
            var scaled = SpeechActivityClock()
            for step in 0..<50 { scaled.update(level: 0.0002 * gain, now: Double(step) / 50) }
            scaled.update(level: 0.04 * gain, now: 1)
            scaled.update(level: 0.04 * gain, now: 1.02)
            precondition(scaled.speaking && scaled.speechCount == 1, "Recognizable quiet speech must not be rejected by a fixed volume cutoff")
            scaled.update(level: 0.0002 * gain, now: 1.3)
            precondition(!scaled.speaking)
        }
        var noise = SpeechActivityClock()
        for step in 0..<100 { noise.update(level: 0.02, now: Double(step) / 50) }
        precondition(!noise.speaking, "Steady background level must not hold the turn open")
        print("PASS: volatile replacement, multi-segment request, finalization, consumed-prefix suppression, next command, invalid ranges")
    }
}
