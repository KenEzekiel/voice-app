"""Subscription worker sidebar identification; simulated screen, no desktop input."""
import argparse
import asyncio
import json
from pathlib import Path
from live_desktop_probe import module, screenshot

async def probe(account):
    b = module.Bridge()
    state = {'epoch': 0, 'revision': 0, 'open': True}
    actions, reports = [], []
    original_emit = module.emit
    def emit(kind, **fields):
        if kind == 'result': reports.append(fields['text'])
    async def ui(operation, **params):
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume': return {'success': params['epoch'] == state['epoch']}
        if operation != 'observe':
            actions.append({'operation': operation, **params})
            if params.get('epoch') != state['epoch'] or params.get('revision') != str(state['revision']):
                return {'success': False, 'error': 'Stale observation'}
            if operation == 'press' and params.get('element') == 2 or (operation == 'click' and 20 <= params.get('x', -1) < 90 and 25 <= params.get('y', -1) < 95):
                state['open'] = not state['open']
            else: return {'success': False, 'error': 'That is Back, not the sidebar control. The left pane remains visible.'}
        state['revision'] += 1
        return {'success': True, 'observation': {
            'app': 'Disposable Editor', 'bundleID': 'local.fixture.editor',
            'epoch': state['epoch'], 'revision': str(state['revision']), 'imageWidth':240, 'imageHeight':120,
            'screenshot': screenshot(False, not state['open']),
            'elements': [{'id':1,'role':'AXButton','AXTitle':'Back','actions':['AXPress']},
                         {'id':2,'role':'AXButton','AXTitle':('Collapse' if state['open'] else 'Expand')+' left sidebar','actions':['AXPress']}],
            'text': f"Disposable GUI fixture: left square is sidebar control, right square is Back. Editor left pane visible={state['open']}. Voice App controller on the right is not a target."}}
    b.ui = ui
    module.emit = emit
    try:
        await b.boot()
        await b.authenticate(account)
        b.worker_thread = await b.start_thread(module.WORKER_INSTRUCTIONS, [module.TOOL])
        b.stopped = False
        await b.utterance('Collapse the left sidebar in the current editor. Not the Back button. Verify the pane is actually collapsed.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        assert not state['open'] and len(actions) == 1 and reports, actions
        return {'passed':True,'actions':actions,'reports':reports,'proof':'Actual subscription worker; synthetic screenshot/AX state and simulated actions. Not a physical Zeron click.'}
    finally:
        await b.close()
        module.emit = original_emit

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=asyncio.run(probe(args.account))
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
