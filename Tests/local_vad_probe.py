"""File-only Silero speech-boundary experiment. No microphone, downloads or GUI access."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time
import wave

import numpy as np
import onnxruntime as ort


MODEL_SHA256 = '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3'


def detect(session, samples):
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    events, durations = [], []
    active = False
    onset = 0
    silence = 0
    for offset in range(0, len(samples), 512):
        chunk = np.zeros((1, 512), dtype=np.float32)
        part = samples[offset:offset + 512]
        chunk[0, :len(part)] = part
        data = np.concatenate((context, chunk), axis=1)
        started = time.perf_counter()
        probability, state = session.run(None, {'input': data, 'state': state,
                                               'sr': np.array(16000, dtype=np.int64)})
        durations.append((time.perf_counter() - started) * 1000)
        context = data[:, -64:]
        score = float(probability[0, 0])
        audio_end = (offset + 512) / 16000
        if not active:
            onset = onset + 1 if score >= 0.5 else 0
            if onset >= 2:
                active = True; silence = 0
                events.append({'event': 'speech_started', 'audioSeconds': round(audio_end, 3)})
        elif score < 0.35:
            silence += 1
            if silence >= 16:  # 512 ms below the release threshold; this is acoustic, not semantic.
                events.append({'event': 'speech_ended', 'audioSeconds': round(audio_end, 3),
                               'silenceHoldMs': 512})
                active = False; onset = 0; silence = 0
        else:
            silence = 0
    return events, durations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError('Unexpected model bytes; inference was not started.')
    with wave.open(str(args.audio)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Use mono 16 kHz signed 16-bit PCM WAV.')
        if not 0 < audio.getnframes() < 16000 * 60:
            raise ValueError('Fixture must be shorter than 60 seconds.')
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.inter_op_num_threads = options.intra_op_num_threads = 1
    started = time.perf_counter()
    session = ort.InferenceSession(str(args.model), providers=['CPUExecutionProvider'], sess_options=options)
    setup_ms = (time.perf_counter() - started) * 1000
    events, times = detect(session, np.concatenate((samples, np.zeros(16000, dtype=np.float32))))
    silence_events, _ = detect(session, np.zeros(16000 * 5, dtype=np.float32))
    passed = ([event['event'] for event in events] == ['speech_started', 'speech_ended'] and not silence_events)
    result = {'proof': 'Real Silero ONNX CPU inference over generated speech and a five-second silence control. Offline frame replay; no wall-clock microphone latency claim.',
              'modelCommit': '5cd7945676eb32225748052e2e6a0580e4686a08', 'modelSha256': MODEL_SHA256,
              'passed': passed, 'events': events, 'silenceFalseStarts': len(silence_events),
              'setupMs': setup_ms, 'medianInferenceMsPer32msFrame': statistics.median(times),
              'maxInferenceMsPer32msFrame': max(times),
              'limitations': 'Acoustic silence is not semantic turn completion. Human speech, pauses, noise, speaker echo and ASR handoff remain unverified.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
