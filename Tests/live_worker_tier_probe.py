"""Opt-in matched Luna tier trial. Synthetic pixels and simulated actions only."""
import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from live_desktop_probe import module, screenshot


PROMPT = 'Click the blue square once. Verify its new color in the returned image, then stop.'


async def probe(account, output):
    rows = []
    bridge = module.Bridge()
    reports = []
    module.emit = lambda kind, **fields: reports.append(fields['text']) if kind == 'result' else None
    state = {'epoch': 0, 'revision': 0, 'clicked': False, 'first': None, 'calls': 0, 'rejections': 0}
    started = 0

    async def ui(operation, **params):
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume':
            return {'success': params.get('epoch') == state['epoch']}
        state['calls'] += 1
        if operation != 'observe':
            valid = (params.get('epoch') == state['epoch']
                     and params.get('revision') == str(state['revision'])
                     and operation == 'click' and not state['clicked']
                     and 150 <= params.get('x', -1) < 220
                     and 25 <= params.get('y', -1) < 95)
            if not valid:
                state['rejections'] += 1
                return {'success': False, 'error': 'Invalid target or stale context. Observe before retrying.'}
            state['first'] = time.monotonic() - started
            state['clicked'] = True
        state['revision'] += 1
        return {'success': True, 'observation': {
            'app': 'Disposable worker tier fixture', 'epoch': state['epoch'],
            'revision': str(state['revision']), 'imageWidth': 240, 'imageHeight': 120,
            'elements': [{'id': i, 'role': 'AXStaticText', 'AXValue': 'Disposable sample text. ' * 12,
                          'actions': []} for i in range(100)],
            'screenshot': screenshot(state['clicked'])}}

    bridge.ui = ui
    result = {'proof': 'Interleaved subscription-backed Luna low-effort trials on identical synthetic '
              'screenshots and simulated clicks. No microphone, desktop capture or OS input.',
              'prompt': PROMPT, 'accountVerified': False, 'trials': rows}

    def save():
        output.write_text(json.dumps(result, indent=2) + '\n')

    try:
        await bridge.boot()
        await bridge.authenticate(account)
        result['accountVerified'] = True
        print('Verified expected ChatGPT account through this app-server connection.', flush=True)
        for pair in range(4):
            for tier in (['default', 'fast'] if pair % 2 == 0 else ['fast', 'default']):
                response = await bridge.rpc.request('thread/start', {
                    'ephemeral': True, 'cwd': str(Path.cwd()), 'approvalPolicy': 'untrusted',
                    'sandbox': 'read-only', 'modelProvider': 'openai', 'model': 'gpt-6-luna',
                    'serviceTier': tier, 'developerInstructions': module.WORKER_INSTRUCTIONS,
                    'dynamicTools': [module.TOOL],
                    'config': {**bridge.disabled_config, 'features.fast_mode': True}, 'environments': []})
                actual_tier = response.get('serviceTier')
                actual_model = response.get('model')
                row = {'pair': pair + 1, 'requestedTier': tier,
                       'returnedTier': actual_tier, 'returnedModel': actual_model}
                rows.append(row)
                allowed = ('fast', 'priority') if tier == 'fast' else ('default', 'standard')
                if actual_model != 'gpt-6-luna' or actual_tier not in allowed:
                    row['failure'] = 'Server did not confirm requested model/tier; no trial dispatched.'
                    save()
                    return result
                bridge.worker_thread = response['thread']['id']
                bridge.stopped = False
                state.update(clicked=False, first=None, calls=0, rejections=0)
                reports.clear()
                started = time.monotonic()
                try:
                    await bridge.utterance(PROMPT)
                    await asyncio.wait_for(bridge.turn_done.wait(), 55)
                    row.update(firstActionSeconds=round(state['first'], 3) if state['first'] else None,
                               completeSeconds=round(time.monotonic() - started, 3),
                               correct=state['clicked'] and bool(reports) and 'green' in reports[-1].lower(),
                               desktopCalls=state['calls'], rejections=state['rejections'])
                except asyncio.TimeoutError:
                    row.update(correct=False, failure='No completed turn within 55 seconds')
                    await bridge.quiesce(sticky=True)
                print(json.dumps(row), flush=True)
                save()
        result['allCorrect'] = all(row.get('correct') for row in rows)
        result['summary'] = {}
        for tier in ('default', 'fast'):
            valid = [row for row in rows if row['requestedTier'] == tier and row.get('correct')]
            result['summary'][tier] = {'correct': len(valid), 'trials': 4,
                'medianFirstActionSeconds': statistics.median(row['firstActionSeconds'] for row in valid) if valid else None,
                'medianCompleteSeconds': statistics.median(row['completeSeconds'] for row in valid) if valid else None}
        save()
        return result
    except Exception as error:
        result['failure'] = type(error).__name__
        save()
        raise
    finally:
        await bridge.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.account, args.output))
