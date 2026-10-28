"""Fresh subscription-worker app switching, entirely simulated. No app contents or OS input."""
import argparse
import asyncio
import json
from pathlib import Path
from live_desktop_probe import module, screenshot

async def probe(account):
    b = module.Bridge()
    state = {'epoch': 0, 'revision': 0, 'pid': 102}
    actions, reports, results = [], [], []
    original_emit = module.emit
    module.emit = lambda kind, **fields: reports.append(fields['text']) if kind == 'result' else None
    async def ui(operation, **params):
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume':
            return {'success': params.get('epoch') == state['epoch']}
        if operation != 'observe':
            if operation != 'activate' or params.get('pid') != 101:
                return {'success': False, 'error': 'Only switching to the observed WhatsApp fixture is allowed.'}
            if params.get('epoch') != state['epoch'] or params.get('revision') != str(state['revision']):
                return {'success': False, 'error': 'Stale observation; observe again.'}
            actions.append('activate')
            state['pid'] = 101
        state['revision'] += 1
        return {'success': True, 'observation': {
            'app': 'WhatsApp' if state['pid'] == 101 else 'Zeron', 'pid': state['pid'],
            'epoch': state['epoch'], 'revision': str(state['revision']),
            'apps': [{'pid': 101, 'name': 'WhatsApp', 'bundleID': 'test.fixture.messenger'},
                     {'pid': 102, 'name': 'Zeron', 'bundleID': 'test.fixture.zeron'}],
            'imageWidth': 240, 'imageHeight': 120, 'elements': [],
            'text': 'Disposable simulated desktop. Active application identity is given in app and pid. No messages exist.',
            'screenshot': screenshot(state['pid'] == 101)}}
    b.ui = ui
    try:
        await b.boot()
        await b.authenticate(account)
        for trial in range(2):
            b.worker_thread = await b.start_thread(module.WORKER_INSTRUCTIONS, [module.TOOL])
            b.stopped = False
            state['pid'] = 102
            actions.clear(); reports.clear()
            await b.utterance('Switch to WhatsApp. This is a disposable simulated desktop; do not send any messages.')
            await asyncio.wait_for(b.turn_done.wait(), 60)
            result = {'trial': trial + 1, 'action_count': len(actions),
                      'requested_app_active': state['pid'] == 101, 'report_received': bool(reports)}
            results.append(result)
            print(json.dumps(result), flush=True)
        return {'passed': all(r['action_count'] == 1 and r['requested_app_active'] and r['report_received'] for r in results),
                'trials': results, 'proof': 'Actual subscription inference with fresh worker threads, simulated app list and activation. No physical apps, messages, microphone or desktop input.'}
    finally:
        await b.close()
        module.emit = original_emit

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--account', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    try:
        result = asyncio.run(probe(args.account))
    except Exception as error:
        result = {'passed': False, 'failure': type(error).__name__}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    if not result['passed']: raise SystemExit(1)
