"""Actual subscription session lifecycle with physical audio devices intercepted."""
import argparse
import asyncio
import json
from pathlib import Path
from live_desktop_probe import module

async def probe(account):
    b = module.Bridge()
    events = []
    answer_pending = asyncio.Event()
    hold_answer = True
    original_emit, original_exchange = module.emit, module.NativeAudio.exchange
    module.emit = lambda kind, **fields: events.append((kind, fields)) if kind.startswith('session') else None
    async def no_devices(native, message, expected=None):
        if message['type'] == 'openDevices': return {'type':'devicesOpened'}
        if message['type'] == 'setAudioControls': return {'type':'audioControlsApplied'}
        if message['type'] == 'applyAnswer' and hold_answer:
            answer_pending.set()
            await asyncio.Future()
        return await original_exchange(native, message, expected)
    module.NativeAudio.exchange = no_devices
    async def ui(operation, **params):
        if operation not in ('pause', 'stop'): raise AssertionError('No GUI allowed in lifecycle probe')
        return {'success':True, 'epoch':0}
    b.ui = ui
    try:
        await b.boot()
        await b.authenticate(account)
        starting = asyncio.create_task(b.command({'type':'session','action':'start','generation':1,'expectedAccount':account}))
        await asyncio.wait_for(answer_pending.wait(), 30)
        old_backend = b.rpc.process.pid
        await asyncio.wait_for(b.command({'type':'session','action':'stop','generation':2}), 15)
        await asyncio.gather(starting, return_exceptions=True)
        hold_answer = False
        await b.command({'type':'session','action':'start','generation':3,'expectedAccount':account})
        if not b.voice_live: raise AssertionError('Native start failed')
        backend_replaced = b.rpc.process.pid != old_backend
        old_worker, old_voice = b.worker_thread, b.voice_thread
        b.last_task = 'Disposable simulated previous task'
        await b.command({'type':'session','action':'restart','generation':4,'expectedAccount':account})
        if not b.voice_live: raise AssertionError('Native restart failed')
        changed = b.worker_thread != old_worker and b.voice_thread != old_voice and not b.last_task
        await b.command({'type':'session','action':'stop','generation':5})
        stopped = not b.voice_live and b.native is None and b.worker_thread is None and b.stopped
        generations = [fields['generation'] for kind, fields in events if kind == 'sessionStarted']
        result = {'passed':backend_replaced and changed and stopped and generations == [3,4],
                  'backend_replaced_after_canceled_start':backend_replaced,
                  'new_worker_and_voice_on_restart':changed, 'stopped_and_native_terminated':stopped,
                  'started_generations':generations,
                  'proof':'Canceled before native SDP application, then actual subscription WebRTC start, restart and stop. Physical microphone/speakers intercepted; no screenshots or GUI actions.'}
        return result
    finally:
        await b.close()
        module.emit, module.NativeAudio.exchange = original_emit, original_exchange

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--account', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    try:
        result = asyncio.run(probe(args.account))
    except Exception as error:
        result = {'passed':False, 'failure':type(error).__name__, 'proof':'Native lifecycle probe failed; no fallback attempted.'}
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    if not result['passed']: raise SystemExit(1)
