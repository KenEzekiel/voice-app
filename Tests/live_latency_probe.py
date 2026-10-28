"""Matched subscription worker timing on synthetic pixels. No microphone or GUI input."""
import argparse
import ast
import asyncio
import json
import time
from pathlib import Path
from live_desktop_probe import module, screenshot

async def probe(account, baseline_path):
    b = module.Bridge()
    state = {'epoch':0,'revision':0,'clicked':False,'observes':0}
    results, reports = [], []
    started = 0
    first = None
    original_emit = module.emit
    def emit(kind, **fields):
        if kind == 'result': reports.append(fields['text'])
    async def ui(operation, **params):
        nonlocal first
        if operation in ('pause','stop'):
            state['epoch'] += 1
            return {'success':True,'epoch':state['epoch']}
        if operation == 'resume': return {'success':params['epoch']==state['epoch']}
        if operation == 'observe': state['observes'] += 1
        if operation != 'observe':
            if params.get('epoch') != state['epoch'] or params.get('revision') != str(state['revision']):
                return {'success':False,'error':'Stale observation'}
            if operation != 'click' or not (150 <= params.get('x',-1) < 220 and 25 <= params.get('y',-1) < 95):
                return {'success':False,'error':'Click the blue square using screenshot pixels.'}
            if first is None: first = time.monotonic()-started
            state['clicked'] = True
        state['revision'] += 1
        return {'success':True,'observation':{'app':'Disposable latency fixture','epoch':state['epoch'],
            'revision':str(state['revision']),'imageWidth':240,'imageHeight':120,'elements':[],
            'text':'Two squares. Inspect their actual color in the screenshot.',
            'screenshot':screenshot(state['clicked'])}}
    b.ui=ui; module.emit=emit
    baseline = next(n.value.value for n in ast.parse(Path(baseline_path).read_text()).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'WORKER_INSTRUCTIONS' for t in n.targets))
    if baseline == module.WORKER_INSTRUCTIONS:
        raise RuntimeError("Before/after worker instructions are identical; use the saved baseline.")
    variant=None
    observed_model=None
    try:
        await b.boot();await b.authenticate(account)
        original=b.rpc.request
        async def request(method,params,**kwargs):
            nonlocal observed_model
            if method=='thread/start' and variant == 'before': params={**params,'developerInstructions':baseline}
            value=await original(method,params,**kwargs)
            if method=='thread/start': observed_model=value.get('model')
            return value
        b.rpc.request=request
        for trial in range(2):
            for candidate in (['before','after'] if trial == 0 else ['after','before']):
                variant=candidate
                b.worker_thread=await b.start_thread(module.WORKER_INSTRUCTIONS,[module.TOOL])
                state.update(clicked=False, observes=0);reports.clear();first=None;b.stopped=False
                started=time.monotonic()
                await b.utterance('Click the blue square once. Verify its new color from the returned image, then stop.')
                timed_out = False
                try:
                    await asyncio.wait_for(b.turn_done.wait(),75)
                except asyncio.TimeoutError:
                    timed_out = True
                row={'trial':trial+1,'variant':candidate,'timeout':timed_out,'actual_model':observed_model,
                     'first_action_seconds':round(first,3) if first is not None else None,
                     'complete_seconds':round(time.monotonic()-started,3),
                     'observe_calls_including_controller':state['observes'],
                     'correct':not timed_out and state['clicked'] and bool(reports) and 'green' in reports[-1].lower()}
                results.append(row);print(json.dumps(row),flush=True)
                if timed_out: break
        return {'trials':results,'proof':'Same subscription model, interleaved before/after worker instructions; matched synthetic screenshot and simulated click/readback. No physical speech or GUI latency measured.'}
    finally:
        await b.close();module.emit=original_emit

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline', default='.build/latency-before/bridge.py')
    args = parser.parse_args()
    try:
        result = asyncio.run(probe(args.account, args.baseline))
        passed = all(row['correct'] for row in result['trials'])
    except Exception as error:
        result = {'passed':False, 'failure':type(error).__name__,
                  'proof':'Benchmark failed; no latency improvement established.'}
        passed = False
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    if not passed: raise SystemExit(1)
