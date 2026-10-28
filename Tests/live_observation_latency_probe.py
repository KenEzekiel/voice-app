"""Matched subscription benchmark for duplicate observation removal. Synthetic pixels only."""
import argparse
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from live_desktop_probe import screenshot


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def probe(account, baseline):
    root = Path(__file__).resolve().parents[1]
    modules = {'before': load(baseline, 'before_bridge'),
               'after': load(root / 'Sources/bridge.py', 'after_bridge')}
    if Path(baseline).read_bytes() == (root / 'Sources/bridge.py').read_bytes():
        raise AssertionError('Baseline and candidate must differ')
    rows = []
    for trial in range(2):
        # Reverse order on the second pair to reduce time/order bias.
        for variant in (['before', 'after'] if trial == 0 else ['after', 'before']):
            module = modules[variant]
            reports = []
            module.emit = lambda kind, **fields: reports.append(fields['text']) if kind == 'result' else None
            b = module.Bridge()
            state = {'epoch': 0, 'revision': 0, 'clicked': False}
            first = None
            start = 0
            measured_model = None
            async def ui(operation, **params):
                nonlocal first
                if operation in ('pause', 'stop'):
                    state['epoch'] += 1
                    return {'success': True, 'epoch': state['epoch']}
                if operation == 'resume':
                    return {'success': params['epoch'] == state['epoch']}
                if operation != 'observe':
                    if params.get('epoch') != state['epoch'] or params.get('revision') != str(state['revision']):
                        return {'success': False, 'error': 'Stale observation'}
                    if operation != 'click' or not (150 <= params.get('x', -1) < 220 and 25 <= params.get('y', -1) < 95):
                        return {'success': False, 'error': 'Click the blue square using screenshot pixels.'}
                    first = first or time.monotonic() - start
                    state['clicked'] = True
                state['revision'] += 1
                return {'success': True, 'observation': {
                    'app': 'Disposable latency fixture', 'epoch': state['epoch'], 'revision': str(state['revision']),
                    'imageWidth': 240, 'imageHeight': 120,
                    'elements': [{'id': i, 'role': 'AXStaticText', 'AXValue': 'Disposable sample text. ' * 12, 'actions': []} for i in range(100)],
                    'screenshot': screenshot(state['clicked'])}}
            b.ui = ui
            try:
                await b.boot()
                await b.authenticate(account)
                original = b.rpc.request
                async def request(method, params, **kwargs):
                    nonlocal measured_model
                    result = await original(method, params, **kwargs)
                    if method == 'thread/start': measured_model = result.get('model')
                    return result
                b.rpc.request = request
                b.worker_thread = await b.start_thread(module.WORKER_INSTRUCTIONS, [module.TOOL])
                b.stopped = False
                for turn in range(2):
                    state['clicked'] = False
                    reports.clear()
                    first = None
                    start = time.monotonic()
                    await b.utterance('Click the blue square once. Verify its new color in the returned image, then stop.')
                    await asyncio.wait_for(b.turn_done.wait(), 60)
                    row = {'variant': variant, 'trial': trial + 1, 'turn': turn + 1, 'model': measured_model,
                           'first_action_seconds': round(first, 3) if first else None,
                           'complete_seconds': round(time.monotonic() - start, 3),
                           'correct': state['clicked'] and bool(reports) and 'green' in reports[-1].lower()}
                    rows.append(row)
                    print(json.dumps(row), flush=True)
            finally:
                await b.close()
    return {'passed': len(rows) == 8 and all(r['correct'] for r in rows), 'trials': rows,
            'proof': 'Same subscription model, interleaved duplicate/single observation variants, synthetic 100-node AX text and pixels, two consecutive turns per thread. No microphone or real desktop input.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(probe(args.account, args.baseline))
    except Exception as error:
        result = {'passed': False, 'failure': type(error).__name__, 'proof': 'Benchmark failed; no speedup established.'}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    if not result['passed']: raise SystemExit(1)
