"""Native subscription idle renewal; no physical audio devices or GUI input."""
import argparse
import asyncio
import json
from pathlib import Path
from live_native_probe import module

async def probe(account):
    b=module.Bridge()
    original_emit=module.emit
    module.emit=lambda *args, **kwargs: None
    original=module.NativeAudio.exchange
    async def no_devices(native,message,expected=None):
        if message['type'] in ('openDevices','setAudioControls'): return {'type':expected}
        return await original(native,message,expected)
    async def ui(operation,**kwargs):
        if operation not in ('pause','stop'): raise AssertionError('No GUI work allowed in this probe')
        return {'success':True,'epoch':1}
    b.ui=ui
    module.NativeAudio.exchange=no_devices
    try:
        await b.boot();await b.start_voice(account)
        old_worker,old_session=b.worker_thread,b.session
        b.stopped=True
        await b.controls(True,True)
        b.last_task='Disposable previous task'
        b.idle_activity(dirty=True)
        deadline=b.idle_timer.when()-asyncio.get_running_loop().time()
        assert abs(deadline-b.idle_minutes*60)<1
        await b.reset_idle_session(b.idle_generation)
        assert b.worker_thread!=old_worker and b.session!=old_session and b.voice_live
        assert b.stopped and b.microphone_muted and not b.last_task
        assert not b.session_dirty and b.idle_timer is None
        return {'passed':True,'configured_minutes':b.idle_minutes,'scheduled_seconds':round(deadline,2),
                'new_worker_and_voice_session':True,'stop_and_mute_preserved':True,'old_task_cleared':True,
                'proof':'Actual subscription handshake and fresh worker/native relay. Expiry handler invoked directly; did not wait five minutes. Physical device opens intercepted; no GUI input.'}
    finally:
        await b.end_voice();await b.close();module.NativeAudio.exchange=original;module.emit=original_emit

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--account',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    try:
        result=asyncio.run(probe(args.account))
    except Exception as error:
        result={'passed':False,'failure':type(error).__name__,'proof':'Native reset probe failed; no API fallback or physical audio/GUI input.'}
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if not result['passed']: raise SystemExit(1)
