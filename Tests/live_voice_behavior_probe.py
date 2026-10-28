#!/usr/bin/env python3
"""Opt-in native voice to desktop routing probe; simulated screen and no audio devices."""
import argparse
import asyncio
import json
from pathlib import Path

from live_desktop_probe import screenshot
from live_native_probe import module


async def probe(account):
    b = module.Bridge()
    state = {'epoch': 0, 'revision': 0, 'sent': False}
    actions, reports = [], []
    started = asyncio.Event()

    async def ui(operation, **params):
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume':
            return {'success': params.get('epoch') == state['epoch']}
        if operation != 'observe':
            assert params.get('epoch') == state['epoch']
            assert params.get('revision') == str(state['revision'])
            assert operation == 'press' and params.get('element') == 0 or (
                operation == 'click' and 150 <= params.get('x', -1) < 220
                and 25 <= params.get('y', -1) < 95)
            actions.append(operation)
            state['sent'] = True
        state['revision'] += 1
        return {'success': True, 'observation': {
            'app': 'Disposable local simulator', 'epoch': state['epoch'],
            'revision': str(state['revision']), 'screenshot': screenshot(state['sent']),
            'text': 'Local fixture, no external service. ' + ('Sent confirmation visible.' if state['sent'] else 'Blue Send button on the right, unsent.'),
            'elements': [{'id': 0, 'role': 'AXButton', 'AXTitle': 'Send', 'actions': ['AXPress']}],
        }}
    b.ui = ui
    original_event = b.on_event

    async def event(message):
        params = message.get('params', {})
        if params.get('threadId') == b.worker_thread:
            if message.get('method') == 'turn/started': started.set()
            item = params.get('item', {})
            if item.get('type') == 'agentMessage' and item.get('phase') != 'commentary':
                reports.append(item.get('text', ''))
        await original_event(message)
    b.on_event = event
    original_exchange = module.NativeAudio.exchange

    async def without_devices(native, message, expected=None):
        if message['type'] in ('openDevices', 'setAudioControls'):
            return {'type': expected}
        return await original_exchange(native, message, expected)
    module.NativeAudio.exchange = without_devices
    try:
        await b.boot()
        await b.start_voice(account)
        await b.rpc.request('thread/realtime/appendText', {
            'threadId': b.voice_thread, 'role': 'user',
            'text': 'Click the blue Send button in the disposable local simulator, then verify the sent confirmation. This fixture submits nothing to any service.'})
        await asyncio.wait_for(started.wait(), 45)
        await asyncio.wait_for(b.turn_done.wait(), 90)
        passed = bool(actions) and state['sent'] and bool(reports)
        return {'passed': passed, 'nativeVoiceDelegatedToDesktopWorker': started.is_set(),
                'simulatedActions': actions, 'workerReports': reports,
                'proof': 'Actual subscription native voice and desktop worker; text input, synthetic screenshot and simulated Send only.',
                'microphoneOpened': False, 'actualGUIClickVerified': False,
                'physicalAudioQualityVerified': False, 'apiFallback': False}
    except (asyncio.TimeoutError, RuntimeError) as error:
        return {'passed': False, 'failure': type(error).__name__,
                'nativeVoiceDelegatedToDesktopWorker': started.is_set(),
                'simulatedActions': actions, 'workerReports': reports,
                'proof': 'No-device native voice probe did not complete. This is not physical voice or GUI proof.',
                'microphoneOpened': False, 'actualGUIClickVerified': False,
                'physicalAudioQualityVerified': False, 'apiFallback': False}
    finally:
        module.NativeAudio.exchange = original_exchange
        await b.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.account))
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']: raise SystemExit(1)
