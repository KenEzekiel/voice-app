#!/usr/bin/env python3
"""Summarize actual local gate logs without reading audio or document text."""
import argparse
import json
from pathlib import Path


def analyze(path):
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    trials = []
    stopped = False
    dispatches = 0
    current = None
    for record in records:
        event = record.get('event')
        if event == 'stopAcknowledged':
            current = {'session': path.name, 'epoch': record['epoch'],
                'stopDelayMs': record['delayMs'], 'dispatchesSincePreviousStop': dispatches,
                'alreadyDispatchedEffectsSettledBeforeAcknowledgement': sum(
                    1 for other in records if other.get('event') == 'actionSettled' and other.get('accepted')
                    and record['requested'] <= other.get('time', -1) <= record['acknowledged']),
                'inFlightAtAcknowledgement': record['inFlightAtAcknowledgement'],
                'newDispatchesAfterAcknowledgement': 0,
                'extraSteering': 'Must be recorded by the observer in Validation/task-trials.json'}
            trials.append(current)
            stopped = True
            dispatches = 0
        elif event == 'resumed':
            stopped = False
        elif event == 'dispatch':
            dispatches += 1
            if stopped and current is not None:
                current['newDispatchesAfterAcknowledgement'] += 1
    return trials


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='*', help='JSONL log files. Defaults to .build/validation/*.jsonl')
    args = parser.parse_args()
    paths = [Path(p) for p in args.logs] if args.logs else sorted(Path('.build/validation').glob('*.jsonl'))
    trials = [trial for path in paths for trial in analyze(path)]
    dispatches_after_ack = sum(t['newDispatchesAfterAcknowledgement'] for t in trials)
    print(json.dumps({'proof': 'Actual gate records; physical voice, task success and steering need separate observation.',
        'stopsRecorded': len(trials), 'stopsWithWorkSincePreviousStop': sum(t['dispatchesSincePreviousStop'] > 0 for t in trials),
        'newDispatchesAfterAcknowledgement': dispatches_after_ack,
        'tenStopTrialsWithWorkRecorded': len([t for t in trials if t['dispatchesSincePreviousStop'] > 0]) >= 10,
        'trials': trials}, indent=2))
