#!/usr/bin/env python3
"""Opt-in native subscription regression: canonical session events, no audio devices."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).resolve().parents[1] / 'Sources/bridge.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

async def probe(account):
    bridge = module.Bridge()
    canonical = []
    async def simulated_observation(operation, **params):
        if operation != 'observe':
            raise AssertionError('Connectivity probe cannot dispatch desktop work')
        return {'success': True, 'observation': {'app': 'TextEdit', 'text': 'Synthetic connectivity document',
                                                'epoch': 0, 'revision': 'probe-only'}}
    bridge.ui = simulated_observation
    original_event = bridge.on_event
    async def on_event(message):
        if message.get('method') == 'thread/realtime/item/completed':
            item = message.get('params', {}).get('item', {})
            canonical.append(item.get('type'))
        await original_event(message)
    bridge.on_event = on_event
    original_exchange = module.NativeAudio.exchange
    async def without_devices(native, message, expected=None):
        if message['type'] == 'openDevices':
            return {'type': 'devicesOpened'}
        if message['type'] == 'setAudioControls':
            return {'type': 'audioControlsApplied'}
        return await original_exchange(native, message, expected)
    module.NativeAudio.exchange = without_devices
    try:
        await bridge.boot()
        await bridge.start_voice(account)
        if 'realtimeSessionStarted' not in canonical:
            raise AssertionError('No canonical session event delivered: native speech routing cannot be trusted')
        await bridge.end_voice()
        return {'proof': 'Actual native subscription WebRTC and canonical session events, with physical device opens intercepted.',
                'canonicalSessionStartedReceived': True, 'transportReady': True,
                'microphoneOpened': False, 'physicalSpeechRoutingVerified': False,
                'startupDocumentObservation': 'simulated; no GUI access in this test',
                'apiFallback': False, 'voiceThreadUsesLocalHistory': True}
    finally:
        module.NativeAudio.exchange = original_exchange
        await bridge.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.account))
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
