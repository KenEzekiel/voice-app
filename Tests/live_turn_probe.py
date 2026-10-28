#!/usr/bin/env python3
"""Opt-in real Codex worker turns against disposable simulated pixels; no OS input or audio."""
import argparse
import asyncio
import json
from pathlib import Path

from live_desktop_probe import module, screenshot


async def probe(account):
    b = module.Bridge(module.SessionTrace(Path('.build/validation/turn-probe')))
    state = {'right': False, 'left': False, 'epoch': 0, 'revision': 0, 'blocked': False}
    actions, reports = [], []
    first_action, pending_action, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    hold_mutation = False
    stop_ack = None
    original_emit = module.emit

    def emit(kind, **fields):
        if kind == 'result': reports.append(fields['text'])

    def observation():
        state['revision'] += 1
        return {'app': 'Disposable local fixture', 'epoch': state['epoch'],
                'revision': str(state['revision']), 'imageWidth': 240, 'imageHeight': 120,
                'text': 'Two clickable squares. Determine colors from the fresh screenshot.',
                'screenshot': screenshot(state['right'], state['left'])}

    async def ui(operation, **params):
        nonlocal stop_ack
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            if operation == 'stop':
                state['blocked'] = True
                stop_ack = len(actions)
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume':
            return {'success': not state['blocked'] and params.get('epoch') == state['epoch']}
        if operation != 'observe':
            if hold_mutation:
                pending_action.set()
                await release.wait()
            if state['blocked'] or params.get('epoch') != state['epoch'] or params.get('revision') != str(state['revision']):
                return {'success': False, 'error': 'Action invalidated. Inspect the screen after resuming.'}
            x, y = params.get('x', -1), params.get('y', -1)
            if operation != 'click' or not 25 <= y < 95 or not (20 <= x < 90 or 150 <= x < 220):
                return {'success': False, 'error': 'Click one visible square using screenshot coordinates.'}
            target = 'left' if x < 90 else 'right'
            state[target] = not state[target]
            actions.append(target)
            first_action.set()
        return {'success': True, 'observation': observation()}

    b.ui = ui
    module.emit = emit
    checks = {}
    try:
        await b.boot()
        await b.authenticate(account)
        b.worker_thread = await b.start_thread(module.WORKER_INSTRUCTIONS, [module.TOOL])
        b.stopped = False
        await b.utterance('Click the blue square once, then verify its new color. This is a disposable simulated GUI, not a website.')
        await asyncio.wait_for(first_action.wait(), 45)
        question_during_execution = b.active_turn is not None
        await b.utterance('What color is the square on the right now? Answer from the current image without clicking anything.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        checks['questionDuringExecution'] = question_during_execution
        checks['questionAnsweredFromCurrentImage'] = bool(reports) and 'green' in reports[-1].lower()
        checks['firstOutcomeCompletedOnce'] = actions == ['right'] and state['right']
        print(json.dumps({'stage': 'question', 'checks': checks}), flush=True)

        await b.utterance('Actually, leave the green square untouched and click the red square on the left once. Verify its new color.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        checks['visibleCorrectionPreservedCompletedWork'] = state['right'] and state['left'] and actions == ['right', 'left']
        checks['correctionVerifiedFromImage'] = bool(reports) and any(c in reports[-1].lower() for c in ('yellow', 'gold'))
        print(json.dumps({'stage': 'correction', 'checks': checks}), flush=True)

        hold_mutation = True
        await b.utterance('Click the green square on the right once, then verify the result.')
        await asyncio.wait_for(pending_action.wait(), 45)
        # Mirrors the UI: close the local gate before requesting model cancellation.
        await ui('stop')
        cancellation = asyncio.create_task(b.command({'type': 'stop'}))
        release.set()
        await asyncio.wait_for(cancellation, 15)
        await asyncio.gather(*list(b.tasks))
        checks['noSimulatedDispatchAfterStopAck'] = stop_ack is not None and len(actions) == stop_ack
        checks['stopPreservedCompletedWork'] = state['right'] and state['left']
        return {'passed': all(checks.values()), 'checks': checks, 'actions': actions, 'reports': reports,
                'proof': 'Actual subscription-backed Codex worker inference and tool protocol, simulated screen and action gate.',
                'physicalSpeechTested': False, 'actualOSInputTested': False,
                'extraSteering': 'Only the outcome, in-flight question, visible correction and explicit Stop; no click-by-click guidance.',
                'apiFallback': False}
    finally:
        release.set()
        await b.close()
        module.emit = original_emit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.account))
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)
