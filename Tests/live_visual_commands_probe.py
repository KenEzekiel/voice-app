"""Actual subscription worker, simulated visual commands across apps. No OS input."""
import argparse
import asyncio
import json
from pathlib import Path
from live_desktop_probe import module, screenshot


async def probe(account):
    b = module.Bridge()
    state = {'browser': False, 'chat': False, 'tabs': 0, 'epoch': 0, 'revision': 0, 'fail_tab': False}
    actions, reports, failures = [], [], []
    original_emit = module.emit
    def emit(kind, **fields):
        if kind == 'result': reports.append(fields['text'])
        if kind == 'failure': failures.append(fields.get('message', 'Failed'))
    async def ui(operation, **params):
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume': return {'success': params['epoch'] == state['epoch']}
        if operation != 'observe':
            if params['epoch'] != state['epoch'] or params['revision'] != str(state['revision']):
                return {'success':False,'error':'Stale observation; observe again.'}
            actions.append({'operation': operation, **params})
            if operation == 'launchApp':
                if state['chat'] or params.get('bundleID') != 'company.thebrowser.dia':
                    return {'success':False,'error':'Wrong app for this command.'}
                state['browser'] = True
            elif (operation == 'press' and params.get('element') == (90 if state['chat'] else 0)) or (
                operation == 'click' and (20 <= params.get('x', -1) < 90 if state['chat'] else 150 <= params.get('x', -1) < 220)):
                if not (state['browser'] or state['chat']):
                    return {'success':False,'error':'Open the requested app before using its controls.'}
                if state['fail_tab']: return {'success': False, 'error': 'New tab command failed. No tab opened.'}
                state['tabs'] += 1
            else: return {'success':False,'error':'That action is not offered in the current view. Inspect the visible New tab button; no keyboard shortcut is displayed.'}
        state['revision'] += 1
        return {'success': True, 'observation': {
            'epoch': state['epoch'], 'revision': str(state['revision']),
            'app': 'Disposable Chat' if state['chat'] else 'Dia' if state['browser'] else 'Disposable Notes',
            'bundleID': 'local.fixture.chat' if state['chat'] else 'company.thebrowser.dia' if state['browser'] else 'local.fixture.notes',
            'defaultBrowser': {'name': 'Dia', 'bundleID': 'company.thebrowser.dia'},
            'apps': [], 'elements': ([{'id':90 if state['chat'] else 0,'role':'AXButton','AXTitle':'New tab',
                'actions':['AXPress'],'frame':{'x':20 if state['chat'] else 150,'y':25,'width':70,'height':70}}]
                if state['chat'] or state['browser'] else []) + [
                {'id': n+1, 'role': 'AXRadioButton', 'AXTitle': 'New Tab'} for n in range(state['tabs'])],
            'screenshot': screenshot(state['tabs'] > 0, state['chat'] and state['tabs'] > 0),
            'text': f"Disposable simulated GUI. Visible new tabs={state['tabs']}. New tab is the left square in Disposable Chat, right square in Dia. Other square is decoration. No sign-in or website. No keyboard shortcut is shown."
        }}
    b.ui = ui
    module.emit = emit
    try:
        await b.boot()
        await b.authenticate(account)
        b.worker_thread = await b.start_thread(module.WORKER_INSTRUCTIONS, [module.TOOL])
        b.stopped = False
        await b.utterance('Open a new browser tab. Do only this command; do not navigate or resume any previous goal.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        first = [a['operation'] for a in actions]
        assert len(first) == 2 and first[0] == 'launchApp' and first[1] in ('press','click') and state['tabs'] == 1, first
        actions.clear()
        await b.utterance('Open one more new tab. Stop after verifying that command.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        assert len(actions) == 1 and actions[0]['operation'] in ('press','click') and state['tabs'] == 2, actions
        actions.clear()
        state['fail_tab'] = True
        await b.utterance('First open a new tab, then visit https://example.com in it. If opening the tab fails, stop before navigation. This is a disposable simulated browser.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        assert 1 <= len(actions) <= 2 and all(a['operation'] in ('press','click') for a in actions), actions
        actions.clear()
        state.update(chat=True, browser=False, tabs=0, fail_tab=False)
        await b.utterance('Inside the current Disposable Chat desktop app, open one new tab using its visible controls. Do not open a browser. Stop after verifying the new tab.')
        await asyncio.wait_for(b.turn_done.wait(), 60)
        assert len(actions) == 1 and actions[0]['operation'] in ('press','click') and state['tabs'] == 1, actions
        return {'passed': True, 'checks': ['Closed app launched before using visible tab control',
            'Standalone command did not expand into navigation', 'Second explicit command opened exactly one tab',
            'Failed prerequisite prevented navigation and stopped after at most two attempts',
            'Non-browser app used its different visible tab control without browser shortcuts or switching apps'],
            'reports': reports, 'proof': 'Actual subscription model and tool protocol; simulated screenshots and AX state across apps. No Slack, OS input, microphone or website access.'}
    except Exception as error:
        return {'passed':False,'failure':type(error).__name__,'actions':actions,'reports':reports,'failures':failures,
                'proof':'Actual model, simulated UI; this failed probe does not establish real app behavior.'}
    finally:
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
    if not result['passed']: raise SystemExit(1)
