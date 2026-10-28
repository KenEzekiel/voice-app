import asyncio
import importlib.util
from pathlib import Path
import unittest
import json
import tempfile

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).resolve().parents[1] / 'Sources/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BrowserScopeTests(unittest.TestCase):
    def setUp(self):
        self.dia = {'bundleID': 'company.thebrowser.dia', 'name': 'Dia'}
        self.chrome = {'bundleID': 'com.google.Chrome', 'name': 'Google Chrome'}
        self.observation = {'defaultBrowser': self.dia, 'browsers': [self.dia, self.chrome],
                            'bundleID': self.dia['bundleID'],
                            'apps': [dict(self.dia, pid=20), dict(self.chrome, pid=21)]}

    def test_default_is_dia_even_when_chrome_is_running(self):
        self.assertEqual(bridge.browser_target('Open Gmail', self.observation), self.dia)

    def test_explicit_browser_overrides_default_but_search_text_does_not(self):
        self.assertEqual(bridge.browser_target('Open Gmail in Google Chrome', self.observation), self.chrome)
        self.assertEqual(bridge.browser_target('Use Chrome instead', self.observation), self.chrome)
        self.assertEqual(bridge.browser_target('Search for Google Chrome features', self.observation), self.dia)

    def test_chrome_launch_activation_and_input_are_rejected(self):
        for action in [{'operation': 'launchApp', 'bundleID': self.chrome['bundleID']},
                       {'operation': 'activate', 'pid': 21}]:
            self.assertIsNotNone(bridge.browser_action_error(action, self.dia, self.observation))
        chrome_screen = dict(self.observation, bundleID=self.chrome['bundleID'])
        for operation in ['click', 'press', 'key', 'type', 'scroll']:
            self.assertIsNotNone(bridge.browser_action_error({'operation': operation}, self.dia, chrome_screen))
        self.assertIsNone(bridge.browser_action_error({'operation': 'observe'}, self.dia, chrome_screen))
        self.assertIsNone(bridge.browser_action_error({'operation': 'activate', 'pid': 20}, self.dia, chrome_screen))

    def test_nonbrowser_apps_and_selected_browser_are_unchanged(self):
        for action in [{'operation': 'launchApp', 'bundleID': 'com.apple.Notes'},
                       {'operation': 'launchApp', 'bundleID': self.dia['bundleID']},
                       {'operation': 'key', 'key': 't', 'modifiers': ['command']}]:
            self.assertIsNone(bridge.browser_action_error(action, self.dia, self.observation))
        self.assertIsNone(bridge.browser_action_error(
            {'operation': 'launchApp', 'bundleID': 'com.google.Chrome'}, self.chrome, self.observation))

    def test_bundle_identity_is_case_insensitive(self):
        self.assertIsNotNone(bridge.browser_action_error(
            {'operation': 'launchApp', 'bundleID': 'com.google.chrome'}, self.dia, self.observation))

    def test_task_requirement_allows_an_explained_browser_switch(self):
        for action in [{'operation': 'launchApp', 'bundleID': self.chrome['bundleID']},
                       {'operation': 'activate', 'pid': 21}]:
            self.assertIsNone(bridge.browser_action_error(dict(action,
                browserReason='The requested workflow requires the installed Chrome extension.'), self.dia, self.observation))
            self.assertIsNotNone(bridge.browser_action_error(dict(action, browserReason=' '), self.dia, self.observation))
        # A reason cannot authorize typing into a different browser without switching first.
        self.assertIsNotNone(bridge.browser_action_error({'operation': 'type', 'text': 'test', 'browserReason': 'Task requirement'},
            self.dia, dict(self.observation, bundleID=self.chrome['bundleID'])))


class SessionTraceTests(unittest.TestCase):
    def test_pointer_diagnostics_keep_only_bounded_numeric_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = bridge.SessionTrace(Path(directory))
            trace.record('pointer_arguments', operation='click', x=1800, y=50,
                         imageWidth=1600, imageHeight=900, hasCoordinates=True, hasElement=False,
                         text='PRIVATE SCREEN TEXT', element='PRIVATE ID')
            trace.record('pointer_arguments', x='PRIVATE COORDINATE', y=float('nan'), imageWidth=1e20)
            trace.close()
            raw = next(Path(directory).glob('voice-*.jsonl')).read_text()
            self.assertNotIn('PRIVATE', raw)
            records = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(records[0]['x'], 1800)
            self.assertEqual(records[0]['imageWidth'], 1600)
            self.assertTrue(records[0]['hasCoordinates'])
            self.assertNotIn('x', records[1])
            self.assertNotIn('y', records[1])
            self.assertNotIn('imageWidth', records[1])

    def test_idle_writer_blocks_without_polling_and_close_wakes_it(self):
        import threading
        from unittest.mock import patch
        entered = threading.Event()
        timeouts = []
        original = bridge.queue.Queue
        class ObservedQueue(original):
            def get(self, block=True, timeout=None):
                timeouts.append(timeout)
                entered.set()
                return super().get(block, timeout)
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge.queue, 'Queue', ObservedQueue):
            trace = bridge.SessionTrace(Path(directory))
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(timeouts, [None])
            finally:
                trace.close()
            self.assertFalse(trace.writer.is_alive())

    def test_trace_contains_only_approved_metadata_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = bridge.SessionTrace(Path(directory))
            trace.record('transcript_received', role='user', interaction=3,
                         text='PRIVATE DICTATION', screenshot='PRIVATE PIXELS', email='private@example.test')
            trace.record('rpc_failed', operation='turn/start', code='timeout', request=4)
            trace.record('worker_result', operation='PRIVATE OPERATION', code='PRIVATE ERROR')
            trace.record('PRIVATE EVENT')
            trace.close()
            paths = list(Path(directory).glob('voice-*.jsonl'))
            self.assertEqual(len(paths), 1)
            raw = paths[0].read_text()
            self.assertNotIn('PRIVATE', raw)
            self.assertNotIn('private@example.test', raw)
            records = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual([r['event'] for r in records], ['transcript_received', 'rpc_failed', 'worker_result'])
            self.assertEqual(records[0]['interaction'], 3)
            self.assertEqual(records[1]['code'], 'timeout')
            self.assertLessEqual(records[0]['elapsed_ms'], records[1]['elapsed_ms'])
            self.assertIn('utc', records[0])
            self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)

    def test_unwritable_trace_does_not_break_work(self):
        with tempfile.TemporaryDirectory() as directory:
            occupied = Path(directory) / 'file'
            occupied.write_text('keep')
            trace = bridge.SessionTrace(occupied)
            trace.record('bridge_started')
            trace.close()
            self.assertTrue(trace.failed)
            self.assertEqual(occupied.read_text(), 'keep')

    def test_disk_write_failure_does_not_raise_in_the_caller(self):
        from unittest.mock import patch
        original = bridge.os.fdopen
        class BrokenDisk:
            def __init__(self, fd, *args, **kwargs): self.output = original(fd, *args, **kwargs)
            def __enter__(self): return self
            def __exit__(self, *args): self.output.close()
            def write(self, line): raise OSError('PRIVATE DISK ERROR')
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge.os, 'fdopen', BrokenDisk):
            trace = bridge.SessionTrace(Path(directory))
            trace.record('bridge_started')
            trace.close()
            self.assertTrue(trace.failed)

    def test_trace_size_is_bounded_and_reports_the_limit(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge.SessionTrace, 'BYTE_LIMIT', 2048):
            trace = bridge.SessionTrace(Path(directory))
            for _ in range(100): trace.record('bridge_started')
            trace.close()
            path = next(Path(directory).glob('voice-*.jsonl'))
            self.assertLessEqual(path.stat().st_size, 2048)
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['event'], 'trace_limit')


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_mute_preserves_running_worker_and_working_status(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.active_turn = 'turn'
        class Native:
            async def exchange(inner, *args): return {}
        self.b.native = Native()
        with patch.object(bridge, 'emit') as emit:
            await self.b.command({'type': 'audioControls', 'muted': True})
        self.assertEqual(self.b.active_turn, 'turn')
        self.assertTrue(self.b.microphone_muted)
        self.assertEqual(self.calls, [])
        self.assertTrue(any(c.kwargs.get('state') == 'working' for c in emit.call_args_list))
        dispatched = []
        async def ui(**arguments):
            dispatched.append(arguments['operation'])
            return {'success': True}
        self.b.ui = ui
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop',
            'arguments': {'operation': 'press', 'element': 4, 'epoch': 1, 'revision': 'fresh'}}})
        self.assertEqual(dispatched, ['press'])

    async def test_stop_during_mute_confirmation_prevents_replanning(self):
        entered, release = asyncio.Event(), asyncio.Event()
        self.b.voice_live = True
        self.b.speech_paused_work = True
        self.b.last_task = 'Collapse the sidebar'
        class Native:
            async def exchange(inner, *args):
                entered.set()
                await release.wait()
                return {}
        self.b.native = Native()
        mute = asyncio.create_task(self.b.command({'type': 'audioControls', 'muted': True}))
        await entered.wait()
        await self.b.command({'type': 'stop'})
        release.set()
        await mute
        self.assertTrue(self.b.stopped)
        self.assertNotIn('turn/start', self.calls)
        self.assertNotIn('resume', self.calls)

    async def test_mute_replans_work_interrupted_by_unsubmitted_speech(self):
        self.b.voice_live = True
        self.b.active_turn = 'old-turn'
        self.b.last_task = 'Collapse the sidebar'
        self.b.hearing = True
        class Native:
            async def exchange(inner, *args): return {}
        self.b.native = Native()
        await self.b.on_speech()
        self.assertIn('turn/interrupt', self.calls)
        self.calls.clear()
        await self.b.command({'type': 'audioControls', 'muted': True})
        self.assertEqual(self.calls, ['pause', 'observe', 'resume', 'turn/start'])
        self.assertIn('Collapse the sidebar', self.prompt)
        self.assertIn(self.text, self.prompt)
        self.assertEqual(self.b.worker_thread, 'worker')
        self.assertTrue(self.b.microphone_muted)
        self.assertFalse(self.b.hearing)

    async def test_mute_does_not_replay_a_completed_task_or_reopen_stop(self):
        self.b.voice_live = True
        self.b.last_task = 'Previously completed task'
        class Native:
            async def exchange(inner, *args): return {}
        self.b.native = Native()
        await self.b.command({'type': 'audioControls', 'muted': True})
        self.assertEqual(self.calls, [])
        self.b.speech_paused_work = True
        self.b.stopped = True
        await self.b.command({'type': 'audioControls', 'muted': True})
        self.assertEqual(self.calls, [])

    async def test_late_speech_callback_and_watchdog_do_nothing_while_muted(self):
        from unittest.mock import patch, AsyncMock
        self.b.voice_live, self.b.microphone_muted, self.b.hearing = True, True, True
        self.b.active_turn = 'turn'
        self.b.input_item = ('session', 'input')
        with patch.object(bridge.asyncio, 'sleep', AsyncMock()), patch.object(bridge, 'emit') as emit:
            await self.b.on_speech()
            await self.b.transcript_watchdog(self.b.input_item)
        self.assertEqual(self.calls, [])
        emit.assert_not_called()

    async def test_click_element_uses_existing_press_route(self):
        self.b.active_turn = 'turn'
        self.b.browser_observation = {'elements': [{'id': 4, 'actions': ['AXPress']}]}
        dispatched = []
        async def ui(**arguments):
            dispatched.append(arguments)
            return {'success': True}
        self.b.ui = ui
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop',
            'arguments': {'operation': 'click', 'element': 4, 'epoch': 1, 'revision': 'fresh'}}})
        self.assertEqual(dispatched, [{'operation': 'press', 'element': 4, 'epoch': 1, 'revision': 'fresh'}])

    async def test_bad_click_coordinates_return_exact_recovery_before_dispatch(self):
        self.b.active_turn = 'turn'
        self.b.browser_observation = {'imageWidth': 1600, 'imageHeight': 900}
        for fields, expected in [({}, 'x and y'), ({'x': 1800, 'y': 50}, '1600'),
                                 ({'x': 50, 'y': 900}, '900')]:
            self.calls.clear()
            await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop',
                'arguments': {'operation': 'click', 'epoch': 1, 'revision': 'fresh', **fields}}})
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(self.calls[-1]['result']['success'])
            self.assertIn(expected, self.calls[-1]['result']['contentItems'][0]['text'])

    async def test_element_click_requires_supported_action_and_no_coordinate_ambiguity(self):
        self.b.active_turn = 'turn'
        self.b.browser_observation = {'elements': [{'id': 4, 'actions': []}]}
        for fields in [{'element': 4}, {'element': 99}, {'element': 4, 'x': 50, 'y': 50}, {'element': 4, 'button': 'right'}]:
            self.calls.clear()
            await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop',
                'arguments': {'operation': 'click', 'epoch': 1, 'revision': 'fresh', **fields}}})
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(self.calls[-1]['result']['success'])

    async def test_explained_browser_switch_rebinds_only_after_verified_activation(self):
        from unittest.mock import patch
        self.b.active_turn = 'turn'
        dia = {'name': 'Dia', 'bundleID': 'company.thebrowser.dia'}
        chrome = {'name': 'Chrome', 'bundleID': 'com.google.Chrome'}
        self.b.browser_target = dia
        self.b.browser_observation = {'browsers': [dia, chrome], 'apps': [dict(chrome, pid=21)]}
        dispatched = []
        async def ui(**arguments):
            dispatched.append(arguments)
            return {'success': True, 'observation': {'pid': 21, 'bundleID': chrome['bundleID'], 'browsers': [dia, chrome]}}
        self.b.ui = ui
        reason = 'The visible page states that the requested extension requires Chrome.'
        with patch.object(bridge, 'emit') as emit:
            await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop',
                'arguments': {'operation': 'activate', 'pid': 21, 'epoch': 1, 'revision': 'fresh', 'browserReason': reason}}})
        self.assertEqual(self.b.browser_target, chrome)
        self.assertNotIn('browserReason', dispatched[0])
        self.assertTrue(any(reason in call.kwargs.get('message', '') for call in emit.call_args_list))
        for success, observed_pid in [(False, 21), (True, 99)]:
            self.b.browser_target = dia
            async def unverified(**arguments):
                return {'success': success, 'observation': {'pid': observed_pid,
                    'bundleID': chrome['bundleID'], 'browsers': [dia, chrome]}}
            self.b.ui = unverified
            await self.b.tool({'id': 8, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop',
                'arguments': {'operation': 'activate', 'pid': 21, 'epoch': 1, 'revision': 'fresh', 'browserReason': reason}}})
            self.assertEqual(self.b.browser_target, dia)

    async def test_each_request_refreshes_browser_binding_and_worker_context(self):
        original = self.b.ui
        async def ui(operation, **params):
            result = await original(operation, **params)
            if operation == 'observe':
                result['observation'].update({'defaultBrowser': {'name': 'Dia', 'bundleID': 'company.thebrowser.dia'},
                    'browsers': [{'name': 'Dia', 'bundleID': 'company.thebrowser.dia'},
                                 {'name': 'Chrome', 'bundleID': 'com.google.Chrome'}]})
            return result
        self.b.ui = ui
        await self.b.utterance('Open Gmail')
        self.assertEqual(self.b.browser_target['name'], 'Dia')
        self.assertIn('Controller browserTarget', self.prompt)
        await self.b.utterance('Use Chrome instead')
        self.assertEqual(self.b.browser_target['name'], 'Chrome')

    async def test_wrong_browser_is_rejected_before_gui_dispatch(self):
        self.b.active_turn = 'turn'
        self.b.browser_target = {'name': 'Dia', 'bundleID': 'company.thebrowser.dia'}
        self.b.browser_observation = {'browsers': [{'name': 'Chrome', 'bundleID': 'com.google.Chrome'}]}
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn',
            'tool': 'desktop', 'arguments': {'operation': 'launchApp', 'bundleID': 'com.google.Chrome',
                                            'epoch': 1, 'revision': 'fresh'}}})
        self.assertNotIn('launchApp', self.calls)
        self.assertFalse(self.calls[-1]['result']['success'])
        self.assertIn('Dia', self.calls[-1]['result']['contentItems'][0]['text'])

    async def test_first_worker_output_timing_is_once_per_turn_without_text(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = bridge.SessionTrace(Path(directory))
            self.b.trace = trace
            self.b.active_turn = 'turn'
            try:
                for _ in range(2):
                    await self.b.on_event({'method': 'item/agentMessage/delta', 'params': {
                        'threadId': 'worker', 'turnId': 'turn', 'delta': 'PRIVATE OUTPUT'}})
                await self.b.on_event({'method': 'thread/tokenUsage/updated', 'params': {
                    'threadId': 'worker', 'turnId': 'turn', 'tokenUsage': {'last': {
                        'inputTokens': 1200, 'cachedInputTokens': 800, 'outputTokens': 70,
                        'reasoningOutputTokens': 'PRIVATE CONTENT', 'private': 'PRIVATE CONTENT'}}}})
            finally:
                trace.close()
            raw = next(Path(directory).glob('voice-*.jsonl')).read_text()
            self.assertNotIn('PRIVATE', raw)
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual([row['event'] for row in rows], ['worker_output_started', 'worker_usage'])
            self.assertEqual(rows[1]['inputTokens'], 1200)
            self.assertEqual(rows[1]['cachedInputTokens'], 800)
            self.assertEqual(rows[1]['outputTokens'], 70)
            self.assertNotIn('reasoningOutputTokens', rows[1])

    async def test_local_switch_context_reaches_next_worker_request(self):
        self.b.last_local_result = 'Switched to Calculator.'
        await self.b.utterance('What app did you switch to?')
        self.assertIn('The local controller previously verified: Switched to Calculator.', self.prompt)
        self.assertIsNone(self.b.last_local_result)

    async def test_local_app_switch_uses_fresh_lease_and_verifies_pid_without_model(self):
        from unittest.mock import patch
        original = self.b.ui
        async def ui(operation, **params):
            if operation == 'activate':
                self.calls.append(operation)
                self.assertEqual(params, {'pid': 42, 'epoch': self.epoch, 'revision': 'revision'})
                return {'success': True, 'observation': {'pid': 42, 'epoch': self.epoch,
                    'revision': 'after', 'screenshot': 'fixture'}}
            result = await original(operation, **params)
            if operation == 'observe':
                result['observation']['apps'] = [{'name': 'Calculator', 'pid': 42}]
            return result
        self.b.ui = ui
        with patch.object(bridge, 'emit') as emit:
            await self.b.utterance('Switch to Calculator.')
        self.assertNotIn('turn/start', self.calls)
        self.assertEqual(self.calls, ['pause', 'observe', 'resume', 'activate'])
        self.assertTrue(any(c.args == ('result',) and c.kwargs['text'] == 'Switched to Calculator.'
                            for c in emit.call_args_list))

    async def test_local_app_switch_ambiguous_or_multi_step_requests_use_worker(self):
        original = self.b.ui
        async def ui(operation, **params):
            result = await original(operation, **params)
            if operation == 'observe':
                result['observation']['apps'] = [{'name': 'Calculator', 'pid': 42},
                                                 {'name': 'Calculator', 'pid': 43}]
            return result
        self.b.ui = ui
        await self.b.utterance('Switch to Calculator')
        self.assertIn('turn/start', self.calls)
        self.assertIsNone(bridge.running_app_target('Switch to Calculator and type 123',
                          [{'name': 'Calculator', 'pid': 42}]))
        self.assertIsNone(bridge.running_app_target('Can you switch to Calculator?',
                          [{'name': 'Calculator', 'pid': 42}]))

    async def test_local_app_switch_never_reports_success_for_wrong_pid_or_interrupted_result(self):
        from unittest.mock import patch
        original = self.b.ui
        for interrupted in (False, True):
            async def ui(operation, **params):
                if operation == 'activate':
                    self.calls.append(operation)
                    if interrupted: await self.b.quiesce(sticky=True)
                    return {'success': True, 'observation': {'pid': 99}}
                result = await original(operation, **params)
                if operation == 'observe':
                    result['observation']['apps'] = [{'name': 'Calculator', 'pid': 42}]
                return result
            self.b.ui = ui
            self.b.stopped = False
            self.blocked = False
            with patch.object(bridge, 'emit') as emit:
                await self.b.utterance('Switch to Calculator')
            self.assertFalse(any(c.args == ('result',) for c in emit.call_args_list))
            self.assertNotIn('turn/start', self.calls)

    async def asyncSetUp(self):
        self.settings_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_directory.cleanup)
        self.b = bridge.Bridge(settings_path=Path(self.settings_directory.name) / "settings.json")
        self.b.worker_thread = 'worker'
        self.b.stopped = False
        self.calls = []
        self.epoch = 0
        self.text = 'Completed work stays here.'
        self.blocked = False
        async def ui(operation, **params):
            self.calls.append(operation)
            if operation in ('pause', 'stop'):
                self.epoch += 1
                if operation == 'stop': self.blocked = True
                return {'success': True, 'epoch': self.epoch}
            if operation == 'observe':
                return {'success': True, 'observation': {'epoch': self.epoch, 'text': self.text, 'revision': 'revision'}}
            if operation == 'resume':
                return {'success': not self.blocked or params.get('explicit', False)}
        self.b.ui = ui
        class FakeRPC:
            async def request(inner, method, params, **kw):
                self.calls.append(method)
                if method == 'turn/interrupt':
                    self.b.active_turn = None
                    self.b.turn_done.set()
                    return {}
                if method == 'turn/start':
                    self.prompt = params['input'][0]['text']
                    return {'turn': {'id': 'new-turn'}}
            def send(inner, message):
                self.calls.append(message)
        self.b.rpc = FakeRPC()

    async def test_session_lifecycle_stops_startup_before_restarting(self):
        from unittest.mock import patch
        from unittest.mock import AsyncMock
        self.b.recycle_server = AsyncMock()
        entered = asyncio.Event()
        calls = []
        async def close_voice(**kwargs): calls.append('close'); self.b.voice_live = False
        async def open_voice(account):
            calls.append('open'); entered.set()
            if len([x for x in calls if x == 'open']) == 1:
                await asyncio.Event().wait()
            self.b.voice_live = True
        self.b.close_voice, self.b.open_voice = close_voice, open_voice
        with patch.object(bridge, 'emit') as emit:
            first = asyncio.create_task(self.b.command({'type':'session','action':'start','generation':1,'expectedAccount':'test'}))
            await entered.wait()
            await self.b.command({'type':'session','action':'restart','generation':2,'expectedAccount':'test'})
            await asyncio.gather(first, return_exceptions=True)
            started = [c.kwargs['generation'] for c in emit.call_args_list if c.args == ('sessionStarted',)]
            self.assertEqual(started, [2])
            self.assertEqual(calls, ['close','open','close','open'])
            self.b.recycle_server.assert_awaited_once()
            await self.b.command({'type':'session','action':'stop','generation':3})
            self.assertFalse(self.b.voice_live)
            self.assertTrue(self.b.stopped)
            await self.b.command({'type':'session','action':'start','generation':2,'expectedAccount':'test'})
            self.assertEqual(calls, ['close','open','close','open','close'])

    async def test_failed_start_retries_with_fresh_backend_only_on_next_start(self):
        from unittest.mock import patch, AsyncMock
        events = []
        async def close_voice(**kwargs): events.append('close')
        async def open_voice(account):
            events.append('open')
            if events.count('open') == 1: raise RuntimeError('Native transport failed')
        async def recycle(): events.append('new backend')
        self.b.close_voice, self.b.open_voice, self.b.recycle_server = close_voice, open_voice, recycle
        with patch.object(bridge, 'emit') as emit:
            await self.b.command({'type':'session','action':'start','generation':1,'expectedAccount':'test'})
            self.assertEqual(events.count('open'), 1)
            emit.assert_any_call('sessionFailed', generation=1,
                message='Session could not start. Choose Start session to retry with a fresh native connection. No API fallback was attempted.')
            await self.b.command({'type':'session','action':'start','generation':2,'expectedAccount':'test'})
            self.assertLess(events.index('new backend'), len(events) - 1)
            emit.assert_any_call('sessionStarted', generation=2)
            self.assertEqual(events.count('new backend'), 1)

    async def test_recycle_backend_stops_old_reader_and_process_before_boot(self):
        from unittest.mock import patch
        events = []
        async def reader():
            try: await asyncio.Event().wait()
            finally: events.append('reader stopped')
        task = asyncio.create_task(reader())
        await asyncio.sleep(0)
        class OldRPC:
            reader = task
            process = object()
        self.b.rpc = OldRPC()
        async def terminate(process): events.append('process stopped')
        async def boot(): events.append('boot')
        self.b.boot = boot
        with patch.object(bridge, 'terminate', terminate):
            await self.b.recycle_server()
        self.assertEqual(events, ['reader stopped', 'process stopped', 'boot'])

    async def test_ten_start_stop_cancellations_allow_a_final_start(self):
        from unittest.mock import patch, AsyncMock
        entered = asyncio.Event()
        hold = True
        async def close_voice(**kwargs): self.b.voice_live = False
        async def open_voice(account):
            entered.set()
            if hold: await asyncio.Event().wait()
            self.b.voice_live = True
        self.b.close_voice, self.b.open_voice = close_voice, open_voice
        self.b.recycle_server = AsyncMock()
        with patch.object(bridge, 'emit') as emit:
            for trial in range(10):
                entered.clear()
                start = asyncio.create_task(self.b.command({'type':'session','action':'start',
                    'generation':trial * 2 + 1,'expectedAccount':'test'}))
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.wait_for(self.b.command({'type':'session','action':'stop','generation':trial * 2 + 2}), 1)
                await asyncio.gather(start, return_exceptions=True)
            hold = False
            await self.b.command({'type':'session','action':'start','generation':21,'expectedAccount':'test'})
        self.assertTrue(self.b.voice_live)
        self.assertFalse(self.b.voice_lock.locked())
        self.assertEqual(self.b.recycle_server.await_count, 10)
        self.assertEqual([c.kwargs['generation'] for c in emit.call_args_list if c.args == ('sessionStarted',)], [21])

    async def test_stop_session_cancels_pending_start_and_clears_context(self):
        from unittest.mock import patch
        entered = asyncio.Event()
        async def close_voice(**kwargs): self.b.voice_live = False
        async def open_voice(account): entered.set(); await asyncio.Event().wait()
        self.b.close_voice, self.b.open_voice = close_voice, open_voice
        self.b.last_task, self.b.worker_thread = 'old goal', 'old-worker'
        with patch.object(bridge, 'emit') as emit:
            start = asyncio.create_task(self.b.command({'type':'session','action':'start','generation':1,'expectedAccount':'test'}))
            await entered.wait()
            await self.b.command({'type':'session','action':'stop','generation':2})
            await asyncio.gather(start, return_exceptions=True)
            self.assertFalse(any(c.args == ('sessionStarted',) for c in emit.call_args_list))
            emit.assert_any_call('sessionStopped', generation=2)
            self.assertEqual(self.b.last_task, '')
            self.assertIsNone(self.b.worker_thread)

    async def test_stop_session_cancels_an_idle_or_microphone_reconnect(self):
        entered = asyncio.Event()
        async def reconnect():
            async with self.b.voice_transition():
                entered.set()
                await asyncio.Event().wait()
        async def close_voice(**kwargs): self.b.voice_live = False
        self.b.close_voice = close_voice
        pending = asyncio.create_task(reconnect())
        await entered.wait()
        await self.b.command({'type':'session','action':'stop','generation':1})
        self.assertTrue(pending.cancelled())
        self.assertIsNone(self.b.voice_transition_task)

    async def test_idle_timeout_defaults_to_five_and_persists_valid_changes(self):
        from unittest.mock import patch
        self.assertEqual(self.b.idle_minutes, 5)
        with patch.object(bridge, 'emit'):
            await self.b.command({'type':'idleTimeout','minutes':7})
            for invalid in [0, 61, True, '5']:
                await self.b.command({'type':'idleTimeout','minutes':invalid})
        self.assertEqual(self.b.idle_minutes, 7)
        reopened = bridge.Bridge(settings_path=self.b.settings_path)
        self.assertEqual(reopened.idle_minutes, 7)
        self.assertFalse(self.b.stopped)

    async def test_idle_reset_preserves_stop_and_mute_and_replaces_context(self):
        from unittest.mock import patch
        self.b.voice_live, self.b.microphone_muted, self.b.speaker_suppressed = True, True, True
        self.b.stopped = True
        self.b.native = type('Native', (), {'process':object()})()
        self.b.voice_thread, self.b.session = 'old-voice', 'old-session'
        self.b.expected_account = 'account@example.test'
        self.b.last_task = 'Old task'
        self.b.reply = ('Old reply',0,'turn')
        async def fresh(*args, **kwargs): return 'fresh-worker'
        async def reopen(expected, *, reuse_worker=False, muted=False):
            self.assertTrue(reuse_worker and muted)
            self.b.native = object()
            self.b.voice_live = True
            self.b.voice_thread, self.b.session = 'fresh-voice', 'fresh-session'
        self.b.start_thread, self.b.open_voice = fresh, reopen
        self.b.idle_activity(dirty=True)
        with patch.object(bridge, 'terminate'), patch.object(bridge, 'emit') as emit:
            await self.b.reset_idle_session(self.b.idle_generation)
        self.assertTrue(self.b.stopped and self.b.microphone_muted)
        self.assertEqual(self.b.worker_thread, 'fresh-worker')
        self.assertEqual(self.b.last_task, '')
        self.assertIsNone(self.b.reply)
        self.assertFalse(self.b.session_dirty)
        self.assertIsNone(self.b.idle_timer)
        self.assertTrue(any(c.args == ('sessionReset',) for c in emit.call_args_list))

    async def test_idle_reset_defers_active_work_and_aborts_when_speech_arrives(self):
        from unittest.mock import patch
        self.b.voice_live, self.b.speaker_suppressed = True, True
        native = type('Native', (), {'process':object()})()
        self.b.native = native
        self.b.idle_activity(dirty=True)
        self.b.active_turn = 'busy'
        with patch.object(bridge, 'terminate') as terminate, patch.object(bridge, 'emit'):
            await self.b.reset_idle_session(self.b.idle_generation)
            self.assertIs(self.b.native, native)
            self.b.active_turn = None
            original_ui = self.b.ui
            async def speech_during_pause(operation, **params):
                if operation == 'pause': self.b.idle_activity(dirty=True)
                return await original_ui(operation, **params)
            self.b.ui = speech_during_pause
            await self.b.reset_idle_session(self.b.idle_generation)
            terminate.assert_not_called()
        self.assertIs(self.b.native, native)

    async def test_canonical_speech_start_pauses_once_before_cancellation_settles(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread = 'voice'
        self.b.session = 'session'
        cancel_started, release = asyncio.Event(), asyncio.Event()
        async def cancel():
            cancel_started.set()
            await release.wait()
        self.b.cancel_worker = cancel
        event = {'method': 'thread/realtime/item/started', 'params': {'threadId': 'voice', 'item': {
            'type': 'transcriptSegment', 'id': 'input', 'realtimeSessionId': 'session', 'role': 'user', 'text': ''}}}
        with patch.object(bridge, 'emit') as emit:
            await self.b.on_event(event)
            await self.b.on_event(event)
            await self.b.on_event({'method': 'thread/realtime/itemAdded', 'params': {
                'threadId': 'voice', 'item': {'type': 'input_audio_buffer.speech_started'}}})
            try:
                await asyncio.wait_for(cancel_started.wait(), .2)
                self.assertEqual(self.calls, ['pause'])
                self.assertTrue(any(c.args == ('voice',) and c.kwargs.get('state') == 'paused' for c in emit.call_args_list))
            finally:
                release.set()
                await asyncio.gather(*self.b.tasks)

    async def test_live_caption_can_be_submitted_once_without_native_completion(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        item = {'type': 'transcriptSegment', 'id': 'input', 'realtimeSessionId': 'session', 'role': 'user', 'text': ''}
        async def watchdog(key): pass
        self.b.transcript_watchdog = watchdog
        routed = []
        async def route(text, thread, session): routed.append(text)
        self.b.voice_utterance = route
        with patch.object(bridge, 'emit') as emit:
            await self.b.on_event({'method':'thread/realtime/item/started','params':{'threadId':'voice','item':item}})
            await asyncio.gather(*self.b.tasks)
            for ident, text in [('assistant-id', 'Ignored'), ('input', 'Make '), ('input', 'a checklist')]:
                await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{'threadId':'voice','itemId':ident,'delta':text}})
            self.assertEqual(self.b.input_text, 'Make a checklist')
            self.assertEqual(routed, [])
            await self.b.command({'type':'submitTranscript'})
            await self.b.command({'type':'submitTranscript'})
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{'threadId':'voice','item':{**item,'text':'Make a checklist'}}})
            await asyncio.gather(*self.b.tasks)
            self.assertEqual(routed, ['Make a checklist'])
            self.assertTrue(any(c.args == ('transcript',) and c.kwargs.get('text') == 'Make a checklist' and c.kwargs.get('partial') for c in emit.call_args_list))

    async def test_delayed_caption_words_remain_one_request(self):
        from unittest.mock import patch
        self.b.voice_live, self.b.hearing = True, True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item = ('session', 'input')
        routed = []
        async def route(text, *args): routed.append(text)
        async def watchdog(*args): pass
        self.b.voice_utterance, self.b.transcript_watchdog = route, watchdog
        async def delta(text):
            await self.b.on_event({'method':'thread/realtime/item/transcript/delta',
                'params':{'threadId':'voice', 'itemId':'input', 'delta':text}})
        with patch.object(bridge, 'emit'):
            await delta('Open ')
            # This is a real gap observed in native caption delivery, not speech silence.
            await asyncio.sleep(0.85)
            self.assertEqual(routed, [])
            await delta('the browser settings')
            await self.b.submit_heard_request()
        self.assertEqual(routed, ['Open the browser settings'])

    async def test_flat_native_captions_preview_without_duplicate_execution(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        routed = []
        async def route(text, *args): routed.append(text)
        async def watchdog(*args): pass
        self.b.voice_utterance, self.b.transcript_watchdog = route, watchdog
        item = {'type':'transcriptSegment','id':'input','realtimeSessionId':'session',
                'role':'user','text':''}
        async def event(method, **params):
            await self.b.on_event({'method':method, 'params':{'threadId':'voice', **params}})
        with patch.object(bridge, 'emit') as emit:
            await event('thread/realtime/transcript/delta', role='assistant', delta='Ignore')
            await event('thread/realtime/transcript/delta', role='user', delta='Open ')
            self.assertTrue(any(c.args == ('transcript',) and c.kwargs.get('text') == 'Open '
                                for c in emit.call_args_list))
            await event('thread/realtime/item/started', item=item)
            await event('thread/realtime/item/transcript/delta', itemId='input', delta='Open ')
            await event('thread/realtime/transcript/delta', role='user', delta='the browser')
            self.b.arm_transcript_submit()
            self.assertIsNone(self.b.transcript_timer, 'do not send canonical text behind the visible preview')
            await event('thread/realtime/item/transcript/delta', itemId='input', delta='the browser')
            await self.b.submit_heard_request()
            await event('thread/realtime/item/completed', item={**item,'text':'Open the browser'})
            await event('thread/realtime/transcript/done', role='user', text='Open the browser')
            await asyncio.gather(*self.b.tasks)
        captions = [(c.kwargs['text'], c.kwargs.get('partial', False))
                    for c in emit.call_args_list if c.args == ('transcript',)]
        self.assertEqual(captions, [('Open ', True), ('Open the browser', True), ('Open the browser', False)])
        self.assertEqual(routed, ['Open the browser'])

    async def test_preview_does_not_send_incomplete_canonical_text_or_accept_muted_input(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item = ('session', 'input')
        self.b.input_text, self.b.input_preview = 'Open', 'Open the browser'
        routed = []
        async def route(text, *args): routed.append(text)
        self.b.voice_utterance = route
        with patch.object(bridge, 'emit') as emit:
            await self.b.submit_heard_request()
            self.assertEqual(routed, [])
            self.assertEqual(self.b.input_text, 'Open')
            emit.assert_any_call('transcript', role='user', text='Open the browser', partial=True)
            emit.reset_mock()
            for stopped, muted, thread in [(True, False, 'voice'), (False, True, 'voice'),
                                           (False, False, 'old-voice')]:
                self.b.stopped, self.b.microphone_muted = stopped, muted
                await self.b.on_event({'method':'thread/realtime/transcript/delta',
                    'params':{'threadId':thread,'role':'user','delta':' ignored'}})
            self.assertFalse(any(c.args == ('transcript',) for c in emit.call_args_list))
            self.assertEqual(self.b.input_preview, 'Open the browser')

    async def test_caption_idle_auto_submits_once_and_new_words_reset_delay(self):
        from unittest.mock import patch
        self.b.voice_live, self.b.hearing = True, True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item = ('session', 'input')
        routed = []
        async def route(text, *a): routed.append(text)
        self.b.voice_utterance = route
        async def delta(text):
            await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{
                'threadId':'voice','itemId':'input','delta':text}})
        with patch.object(bridge, 'TRANSCRIPT_IDLE_SECONDS', 0.04), patch.object(bridge, 'emit'):
            await delta('Open ')
            await asyncio.sleep(0.025)
            await delta('files panel')
            await asyncio.sleep(0.025)
            self.assertEqual(routed, [])
            await asyncio.sleep(0.03)
            self.assertEqual(routed, ['Open files panel'])
            await self.b.command({'type':'submitTranscript'})
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{'type':'transcriptSegment','role':'user','id':'input',
                'realtimeSessionId':'session','text':'Open files panel'}}})
            await asyncio.sleep(0)
        self.assertEqual(routed, ['Open files panel'])

    async def test_idle_submit_cancelled_by_stop_mute_end_and_manual_send(self):
        from unittest.mock import patch
        for action in ['stop', 'mute', 'end', 'manual']:
            self.b.voice_live, self.b.hearing, self.b.stopped = True, True, False
            self.b.microphone_muted = False
            self.b.voice_thread, self.b.session = 'voice', 'session'
            self.b.input_item, self.b.input_text = ('session','input'), 'Open files panel'
            routed = []
            async def route(text, *a): routed.append(text)
            self.b.voice_utterance = route
            class Native:
                process = object()
                async def exchange(inner, message, expected): return {'type':expected}
            self.b.native = Native()
            with patch.object(bridge, 'TRANSCRIPT_IDLE_SECONDS', 0.02), patch.object(bridge, 'emit'), patch.object(bridge, 'terminate'):
                self.b.arm_transcript_submit()
                if action == 'mute':
                    await self.b.command({'type':'audioControls','muted':True})
                elif action == 'end': await self.b.end_voice()
                else: await self.b.command({'type':'submitTranscript' if action=='manual' else 'stop'})
                # Reopening admission must not resurrect an old timer.
                self.b.stopped = False
                self.b.microphone_muted = False
                await asyncio.sleep(0.04)
            self.assertEqual(routed, ['Open files panel'] if action=='manual' else [], action)

    async def test_idle_submit_handles_two_commands_in_one_segment_without_replay(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item = ('session','input')
        routed = []
        async def route(text, *a): routed.append(text.strip())
        async def watchdog(*a): pass
        self.b.voice_utterance, self.b.transcript_watchdog = route, watchdog
        with patch.object(bridge, 'TRANSCRIPT_IDLE_SECONDS', 0.01), patch.object(bridge, 'emit'):
            for text in ['Open files panel.', ' Close files panel.']:
                await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{
                    'threadId':'voice','itemId':'input','delta':text}})
                await asyncio.sleep(0.03)
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{'type':'transcriptSegment','role':'user','id':'input',
                'realtimeSessionId':'session','text':'Open files panel. Close files panel.'}}})
            await asyncio.sleep(0)
        self.assertEqual(routed, ['Open files panel.', 'Close files panel.'])

    async def test_late_punctuation_does_not_start_or_prefix_the_next_request(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item = ('session', 'input')
        routed = []
        async def route(text, *args): routed.append(text.strip())
        async def watchdog(*args): pass
        self.b.voice_utterance, self.b.transcript_watchdog = route, watchdog
        async def delta(text):
            await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{
                'threadId':'voice','itemId':'input','delta':text}})
        with patch.object(bridge, 'emit') as emit:
            await delta('Open files')
            await self.b.submit_heard_request()
            await asyncio.gather(*self.b.tasks)
            emit.reset_mock()
            for punctuation in ['.', ';', ']']:
                await delta(punctuation)
            self.assertEqual(self.b.input_text, '')
            self.assertFalse(self.b.hearing)
            self.assertFalse(any(c.args == ('transcript',) for c in emit.call_args_list))
            await delta(' Switch to WhatsApp')
            await self.b.submit_heard_request()
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{'type':'transcriptSegment','role':'user','id':'input',
                'realtimeSessionId':'session','text':'Open files.;] Switch to WhatsApp.'}}})
            await asyncio.gather(*self.b.tasks)
        self.assertEqual(routed, ['Open files', 'Switch to WhatsApp'])

    async def test_new_speech_and_stop_invalidate_already_queued_idle_send(self):
        from unittest.mock import patch
        self.b.voice_live, self.b.hearing = True, True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item, self.b.input_text = ('session','input'), 'Open files panel'
        routed = []
        async def route(text, *a): routed.append(text)
        self.b.voice_utterance = route
        with patch.object(bridge, 'emit'):
            self.b.arm_transcript_submit()
            generation = self.b.transcript_generation
            await self.b.on_event({'method':'thread/realtime/itemAdded','params':{
                'threadId':'voice','item':{'type':'input_audio_buffer.speech_started'}}})
            await self.b.submit_heard_request(('session','input'), 'Open files panel', generation)
            self.b.arm_transcript_submit()
            generation = self.b.transcript_generation
            await self.b.command({'type':'stop'})
            self.b.stopped = False
            await self.b.submit_heard_request(('session','input'), 'Open files panel', generation)
        self.assertEqual(routed, [])

    async def test_stalled_transcript_gets_explicit_recovery_without_dispatch(self):
        from unittest.mock import patch, AsyncMock
        self.b.voice_live, self.b.hearing = True, True
        self.b.input_item = ('session','input')
        with patch.object(bridge.asyncio, 'sleep', AsyncMock()), patch.object(bridge, 'emit') as emit:
            await self.b.transcript_watchdog(self.b.input_item)
            emit.assert_called_once()
            self.assertEqual(emit.call_args.kwargs['state'], 'transcribing')
            emit.reset_mock()
            self.b.stopped = True
            await self.b.transcript_watchdog(self.b.input_item)
            emit.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_manual_submission_does_not_swallow_next_command_in_same_native_item(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        routed = []
        async def route(text, thread, session): routed.append(text.strip())
        async def watchdog(key): pass
        self.b.voice_utterance = route
        self.b.transcript_watchdog = watchdog
        item = {'type':'transcriptSegment','id':'shared-input','realtimeSessionId':'session','role':'user','text':''}
        with patch.object(bridge, 'emit'):
            await self.b.on_event({'method':'thread/realtime/item/started','params':{'threadId':'voice','item':item}})
            await asyncio.gather(*self.b.tasks)
            for text in ['Open a tab.', ' Open another tab.']:
                await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{
                    'threadId':'voice','itemId':'shared-input','delta':text}})
                await self.b.command({'type':'submitTranscript'})
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{**item,'text':'Open a tab. Open another tab.'}}})
            await asyncio.gather(*self.b.tasks)
        self.assertEqual(routed, ['Open a tab.', 'Open another tab.'])

    async def test_finalization_does_not_replay_submitted_prefix_or_erase_new_caption(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.submitted_items[('session','old')] = 'Open a tab.'
        self.b.input_item = ('session','new')
        self.b.input_text = 'Type hello'
        self.b.hearing = True
        with patch.object(bridge, 'emit') as emit:
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{'type':'transcriptSegment','id':'old','realtimeSessionId':'session',
                                        'role':'user','text':'Open a tab.'}}})
            self.assertFalse(any(c.args == ('transcript',) for c in emit.call_args_list))
        self.assertEqual(self.b.input_item, ('session','new'))
        self.assertEqual(self.b.input_text, 'Type hello')
        self.assertTrue(self.b.hearing)
        self.assertEqual(self.calls, [])

    async def test_revised_submitted_prefix_does_not_execute_an_arbitrary_tail(self):
        from unittest.mock import patch
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.submitted_items[('session','input')] = 'Open a tab.'
        with patch.object(bridge, 'emit') as emit:
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{'type':'transcriptSegment','id':'input','realtimeSessionId':'session',
                                        'role':'user','text':'Different revised command'}}})
            self.assertTrue(any(c.args == ('actionFailure',) for c in emit.call_args_list))
        self.assertEqual(self.calls, [])

    async def test_completed_command_returns_to_listening_for_the_next_command(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.speaker_suppressed = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        count = 0
        original = self.b.rpc.request
        async def request(method, params, **kwargs):
            nonlocal count
            if method != 'turn/start': return await original(method, params, **kwargs)
            count += 1
            turn = str(count)
            await self.b.on_event({'method':'turn/started','params':{'threadId':'worker','turn':{'id':turn}}})
            await self.b.on_event({'method':'item/completed','params':{'threadId':'worker','turnId':turn,
                'item':{'type':'agentMessage','phase':'final_answer','text':'Verified command done'}}})
            await self.b.on_event({'method':'turn/completed','params':{'threadId':'worker','turn':{'id':turn,'status':'completed'}}})
            return {'turn':{'id':turn}}
        self.b.rpc.request = request
        with patch.object(bridge, 'emit') as emit:
            for ident in ('one','two'):
                await self.b.on_event({'method':'thread/realtime/item/completed','params':{'threadId':'voice',
                    'item':{'type':'transcriptSegment','id':ident,'realtimeSessionId':'session','role':'user','text':'Open a tab'}}})
                await asyncio.gather(*self.b.tasks)
                self.assertEqual([c.kwargs['state'] for c in emit.call_args_list if c.args == ('voice',)][-1], 'listening')
                self.assertFalse(self.b.microphone_muted)
        self.assertEqual(count, 2)

    async def test_late_failure_and_result_from_old_turn_do_not_replace_current_turn(self):
        from unittest.mock import patch
        self.b.active_turn = 'new-turn'
        with patch.object(bridge, 'emit') as emit:
            await self.b.on_event({'method': 'turn/completed', 'params': {
                'threadId': 'worker', 'turn': {'id': 'old-turn', 'status': 'failed'}}})
            await self.b.on_event({'method': 'item/completed', 'params': {
                'threadId': 'worker', 'turnId': 'old-turn', 'item': {
                    'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Stale result'}}})
        self.assertFalse(self.b.stopped)
        self.assertEqual(self.b.active_turn, 'new-turn')
        self.assertFalse(any(c.args[0] in ('failure', 'result') for c in emit.call_args_list))

    async def test_late_image_delivery_failure_cannot_stop_new_turn(self):
        from unittest.mock import patch
        self.b.active_turn = 'old-turn'
        original = self.b.ui
        async def ui(operation, **params):
            result = await original(operation, **params)
            if operation == 'observe': result['observation']['screenshot'] = 'data:image/png;base64,fixture'
            return result
        self.b.ui = ui
        async def request(method, params, **kwargs):
            self.b.active_turn = 'new-turn'
            raise RuntimeError('Late old image failure')
        self.b.rpc.request = request
        with patch.object(bridge, 'emit') as emit:
            await self.b.tool({'id': 5, 'params': {'threadId': 'worker', 'turnId': 'old-turn',
                'tool': 'desktop', 'arguments': {'operation': 'observe'}}})
        self.assertFalse(self.b.stopped)
        self.assertNotIn('stop', self.calls)
        self.assertFalse(any(c.args[0] in ('failure', 'actionFailure') for c in emit.call_args_list))

    async def test_interruption_invalidates_old_results_before_waiting_for_pause_ack(self):
        self.b.active_turn = 'old-turn'
        original = self.b.ui
        async def ui(operation, **params):
            if operation == 'pause': self.assertIn('old-turn', self.b.cancelled_turns)
            return await original(operation, **params)
        self.b.ui = ui
        await self.b.on_speech()

    async def test_local_trace_follows_transcript_desktop_and_spoken_result_without_payloads(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            trace = bridge.SessionTrace(Path(directory))
            self.b.trace = trace
            self.b.voice_live = True
            self.b.voice_thread = 'voice'
            self.b.session = 'session'
            self.b.ui = lambda operation, **params: bridge.Bridge.ui(self.b, operation, **params)
            def emit(kind, **fields):
                if kind == 'desktop':
                    self.b.spawn(self.b.command({'type': 'desktopResult', 'id': fields['id'], 'result': {
                        'success': True, 'observation': {'epoch': 1, 'revision': 'PRIVATE REVISION',
                        'text': 'PRIVATE SCREEN', 'screenshot': 'PRIVATE PIXELS'}}}))
            try:
                with patch.object(bridge, 'emit', emit):
                    await self.b.on_event({'method': 'thread/realtime/item/completed', 'params': {
                        'threadId': 'voice', 'item': {'type': 'transcriptSegment', 'id': 'first',
                        'realtimeSessionId': 'session', 'role': 'user', 'text': 'PRIVATE DICTATION'}}})
                    await asyncio.gather(*self.b.tasks)
                    await self.b.on_event({'method': 'turn/started', 'params': {
                        'threadId': 'worker', 'turn': {'id': 'new-turn'}}})
                    await self.b.tool({'id': 1, 'params': {'threadId': 'worker', 'turnId': 'new-turn',
                        'tool': 'desktop', 'arguments': {'operation': 'type', 'text': 'PRIVATE TYPING', 'epoch': 1, 'revision': 'PRIVATE REVISION'}}})
                    await self.b.on_event({'method': 'item/completed', 'params': {
                        'threadId': 'worker', 'turnId': 'new-turn', 'item': {
                            'type': 'agentMessage', 'phase': 'final_answer', 'text': 'PRIVATE RESULT'}}})
                    await self.b.on_event({'method': 'turn/completed', 'params': {
                        'threadId':'worker', 'turn':{'id':'new-turn','status':'completed'}}})
                    class Native:
                        async def exchange(inner, message, expected): return {'type':expected}
                    self.b.native = Native()
                    await self.b.command({'type':'playReply'})
                    await asyncio.gather(*self.b.tasks)
            finally:
                trace.close()
            raw = next(Path(directory).glob('voice-*.jsonl')).read_text()
            self.assertNotIn('PRIVATE', raw)
            records = [json.loads(line) for line in raw.splitlines()]
            events = [record['event'] for record in records]
            for event in ('transcript_received', 'transcript_routed', 'observation_requested',
                          'desktop_completed', 'worker_start_requested', 'worker_started', 'worker_start_acknowledged',
                          'worker_result', 'speech_requested', 'speech_accepted'):
                self.assertIn(event, events)
            self.assertLess(events.index('transcript_received'), events.index('worker_started'))
            self.assertLess(events.index('worker_result'), events.index('speech_accepted'))
            self.assertTrue(any(r['event'] == 'desktop_completed' and r['operation'] == 'type' for r in records))

    async def test_rpc_timeout_records_a_code_without_request_contents(self):
        class Input:
            def write(self, value): pass
        class Process:
            stdin = Input()
            stdout = asyncio.StreamReader()
        async def event(message): pass
        with tempfile.TemporaryDirectory() as directory:
            trace = bridge.SessionTrace(Path(directory))
            rpc = bridge.RPC(Process(), event, trace)
            try:
                with self.assertRaises(asyncio.TimeoutError):
                    await rpc.request('turn/start', {'input': 'PRIVATE REQUEST'}, timeout=.001)
            finally:
                rpc.reader.cancel()
                await asyncio.gather(rpc.reader, return_exceptions=True)
                trace.close()
            raw = next(Path(directory).glob('voice-*.jsonl')).read_text()
            self.assertNotIn('PRIVATE', raw)
            records = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual([r['event'] for r in records], ['rpc_requested', 'rpc_failed'])
            self.assertEqual(records[-1]['code'], 'timeout')

    async def test_native_frontend_receives_our_prompt_and_no_filler(self):
        from unittest.mock import patch
        self.b.helper = Path('/unused-helper')
        async def authenticate(expected): return {}
        self.b.authenticate = authenticate
        threads_started = set()
        both_started = asyncio.Event()
        async def start_thread(instructions, tools, **kwargs):
            kind = 'worker' if tools else 'voice'
            threads_started.add(kind)
            if len(threads_started) == 2: both_started.set()
            await asyncio.wait_for(both_started.wait(), 1)
            return kind
        self.b.start_thread = start_thread
        class Process:
            returncode = 0
            async def communicate(inner): return (b'test-commit', b'')
            async def wait(inner): await asyncio.Event().wait()
        async def create(*args, **kwargs): return Process()
        class Native:
            def __init__(inner, process, trace=None): inner.process = process
            async def exchange(inner, message, expected=None):
                return {'type': expected, 'sdp': 'test-offer'}
        requests = []
        async def request(method, params, **kwargs):
            requests.append((method, params))
            if method == 'thread/realtime/start':
                for event in [
                    {'method': 'thread/realtime/started', 'params': {'version': 'v3', 'realtimeSessionId': self.b.session}},
                    {'method': 'thread/realtime/sdp', 'params': {'sdp': 'test-answer'}},
                ]: self.b.voice_events.put_nowait(event)
            return {}
        self.b.rpc.request = request
        with patch.object(bridge.asyncio, 'create_subprocess_exec', create), patch.object(bridge, 'NativeAudio', Native):
            await self.b.start_voice('account@example.test')
        self.b.voice_live, self.b.native, self.b.stopped = False, None, True
        self.b.last_task = 'Preserved outcome'
        threads_started.clear()
        with patch.object(bridge.asyncio, 'create_subprocess_exec', create), patch.object(bridge, 'NativeAudio', Native):
            await self.b.open_voice('account@example.test', reuse_worker=True)
        self.assertEqual(threads_started, {'voice'})
        self.assertEqual(self.b.worker_thread, 'worker')
        self.assertTrue(self.b.stopped)
        self.assertEqual(self.b.last_task, 'Preserved outcome')
        params = next(p for m, p in requests if m == 'thread/realtime/start')
        self.assertEqual(params.get('prompt'), bridge.VOICE_INSTRUCTIONS)
        self.assertEqual(params.get('realtimeStartInstructions'), bridge.VOICE_RELAY_INSTRUCTIONS)
        self.assertIs(params.get('delegationAckFiller'), False)
        # A failed parallel branch must cancel and settle the other branch.
        with patch.object(bridge, 'terminate', lambda process: asyncio.sleep(0)):
            await self.b.end_voice()
        settled = asyncio.Event()
        async def failed_threads(instructions, tools, **kwargs):
            if tools:
                await asyncio.sleep(0)
                raise RuntimeError('Synthetic setup failure')
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        self.b.start_thread = failed_threads
        with patch.object(bridge.asyncio, 'create_subprocess_exec', create), patch.object(bridge, 'NativeAudio', Native):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic setup failure'):
                await self.b.start_voice('account@example.test')
        self.assertTrue(settled.is_set())

    async def test_interruption_discards_a_queued_spoken_result(self):
        self.b.voice_live = True
        self.b.voice_thread = 'voice'
        ticket = self.b.interaction
        self.b.interaction += 1
        await self.b.speak_result('Old completion', ticket, 'old-turn')
        self.assertNotIn('thread/realtime/appendSpeech', self.calls)

    async def test_one_completed_result_requires_explicit_play_with_acknowledged_mic_mute(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.active_turn = 'turn'
        self.b.speaker_suppressed = True
        controls = []
        class Native:
            async def exchange(inner, message, expected):
                controls.append(message['controls'])
                return {'type': expected}
        self.b.native = Native()
        with patch.object(bridge, 'emit') as emit:
            for text in ['Earlier final item', 'Final verified answer']:
                await self.b.on_event({'method':'item/completed','params':{'threadId':'worker','turnId':'turn',
                    'item':{'type':'agentMessage','phase':'final_answer','text':text}}})
            self.assertFalse(any(c.args == ('result',) for c in emit.call_args_list))
            for _ in range(2):
                await self.b.on_event({'method':'turn/completed','params':{'threadId':'worker','turn':{'id':'turn','status':'completed'}}})
            results = [c.kwargs['text'] for c in emit.call_args_list if c.args == ('result',)]
            self.assertEqual(results, ['Final verified answer'])
            self.assertNotIn('thread/realtime/appendSpeech', self.calls)
            original = self.b.rpc.request
            async def request(method, params, **kwargs):
                if method == 'thread/realtime/appendSpeech':
                    self.assertTrue(self.b.microphone_muted)
                    self.assertFalse(self.b.speaker_suppressed)
                return await original(method, params, **kwargs)
            self.b.rpc.request = request
            await self.b.command({'type':'playReply'})
            await self.b.command({'type':'playReply'})
        self.assertEqual(self.calls.count('thread/realtime/appendSpeech'), 1)
        self.assertEqual(controls, [{'microphoneMuted':True,'speakerSuppressed':False}])

    async def test_speaker_feedback_is_discarded_during_playback_and_after_reopening_mic(self):
        from unittest.mock import patch
        self.b.voice_live, self.b.microphone_muted = True, True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        item = {'type':'transcriptSegment','role':'user','id':'echo','realtimeSessionId':'session','text':'Repeated error'}
        with patch.object(bridge, 'emit') as emit:
            await self.b.on_event({'method':'thread/realtime/item/started','params':{'threadId':'voice','item':item}})
            self.b.microphone_muted = False
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{'threadId':'voice','item':item}})
            self.assertFalse(any(c.args == ('transcript',) for c in emit.call_args_list))
        self.assertEqual(self.calls, [])
        self.assertFalse(self.b.hearing)

    async def test_listen_again_cannot_open_speakers_and_microphone_together(self):
        class Native:
            async def exchange(inner, message, expected):
                self.assertEqual(message['controls'], {'microphoneMuted':False,'speakerSuppressed':True})
                return {'type':expected}
        self.b.native, self.b.voice_live = Native(), True
        await self.b.command({'type':'audioControls','muted':False,'suppressed':False})
        self.assertFalse(self.b.microphone_muted)
        self.assertTrue(self.b.speaker_suppressed)

    async def test_listen_again_keeps_native_session_and_active_worker(self):
        from unittest.mock import patch, AsyncMock
        self.b.voice_live, self.b.microphone_muted = True, True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.active_turn, self.b.last_task = 'in-progress', 'Keep completed work'
        native = type('Native', (), {'exchange': AsyncMock(return_value={'type':'audioControlsApplied'})})()
        self.b.native = native
        with patch.object(bridge, 'emit'), patch.object(bridge, 'terminate') as terminate:
            await self.b.command({'type':'audioControls','muted':False})
        terminate.assert_not_called()
        native.exchange.assert_awaited_once()
        self.assertIs(self.b.native, native)
        self.assertEqual((self.b.voice_thread, self.b.session), ('voice', 'session'))
        self.assertEqual(self.b.active_turn, 'in-progress')
        self.assertEqual(self.b.last_task, 'Keep completed work')
        self.assertEqual(self.calls, [])
        self.assertFalse(self.b.microphone_muted)

    async def test_same_segment_continues_after_mute_without_replaying_buffered_words(self):
        from unittest.mock import patch, AsyncMock
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.input_item, self.b.input_text = ('session','input'), 'Discard before mute'
        self.b.native = type('Native', (), {'exchange': AsyncMock()})()
        routed = []
        async def route(text, *a): routed.append(text.strip())
        self.b.voice_utterance = route
        with patch.object(bridge, 'emit'):
            await self.b.command({'type':'audioControls','muted':True})
            await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{
                'threadId':'voice','itemId':'input','delta':' buffered'}})
            await self.b.command({'type':'audioControls','muted':False})
            await self.b.on_event({'method':'thread/realtime/item/transcript/delta','params':{
                'threadId':'voice','itemId':'input','delta':' Open files'}})
            await self.b.submit_heard_request()
            await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                'threadId':'voice','item':{'type':'transcriptSegment','role':'user','id':'input',
                'realtimeSessionId':'session','text':'Discard before mute buffered Open files'}}})
            await asyncio.gather(*list(self.b.tasks))
        self.assertEqual(routed, ['Open files'])

    async def test_orphan_punctuation_cannot_prefix_a_new_caption(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'voice', 'session'
        self.b.hearing = True
        with patch.object(bridge, 'emit') as emit:
            for delta in ['.', ' Open browser and open TikTok']:
                await self.b.on_event({'method':'thread/realtime/transcript/delta','params':{
                    'threadId':'voice','role':'user','delta':delta}})
        captions = [c.kwargs['text'] for c in emit.call_args_list if c.args == ('transcript',)]
        self.assertEqual(captions, ['Open browser and open TikTok'])
        self.assertEqual(bridge.caption_text('.env'), '.env')
        self.assertEqual(bridge.caption_text('Open example.com.'), 'Open example.com.')

    async def test_reconnected_voice_discards_old_session_and_accepts_new_speech(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.voice_thread, self.b.session = 'old-voice', 'old-session'
        self.b.microphone_muted = True
        await self.b.on_event({'method':'thread/realtime/item/started','params':{
            'threadId':'old-voice','item':{'type':'transcriptSegment','role':'user',
            'id':'input','realtimeSessionId':'old-session','text':''}}})
        self.assertIn(('old-session', 'input'), self.b.submitted_items)
        self.b.voice_thread, self.b.session = 'new-voice', 'new-session'
        self.b.microphone_muted = False
        routed = []
        async def utterance(text, *a): routed.append(text)
        self.b.voice_utterance = utterance
        with patch.object(bridge, 'emit'):
            for thread, session, text in [('old-voice','old-session','Buffered speech'),
                                           ('new-voice','new-session','Open files panel')]:
                await self.b.on_event({'method':'thread/realtime/item/completed','params':{
                    'threadId':thread,'item':{'type':'transcriptSegment','role':'user',
                    'id':'input','realtimeSessionId':session,'text':text}}})
            await asyncio.gather(*list(self.b.tasks))
        self.assertEqual(routed, ['Open files panel'])

    async def test_stale_play_button_releases_pending_state_without_speech(self):
        from unittest.mock import patch
        self.b.voice_live = True
        self.b.speaker_suppressed = True
        with patch.object(bridge, 'emit') as emit:
            await self.b.command({'type':'playReply'})
            emit.assert_called_once_with('audioControls', muted=False, suppressed=True)
        self.assertEqual(self.calls, [])

    async def test_playback_request_failure_closes_speaker_and_keeps_mic_muted(self):
        class Native:
            async def exchange(inner, message, expected): return {'type':expected}
        self.b.native, self.b.voice_live = Native(), True
        self.b.reply = ('Reply', self.b.interaction, 'turn')
        async def failed(*args, **kwargs): raise TimeoutError('not accepted')
        self.b.rpc.request = failed
        with self.assertRaisesRegex(RuntimeError, 'Reply playback failed'):
            await self.b.command({'type':'playReply'})
        self.assertTrue(self.b.microphone_muted)
        self.assertTrue(self.b.speaker_suppressed)

    async def test_audio_boundary_rejects_duplex_even_for_a_direct_caller(self):
        with self.assertRaisesRegex(RuntimeError, 'cannot open microphone and speakers together'):
            await self.b.controls(False, False)

    async def test_silenced_replies_do_not_queue_speech_for_later(self):
        self.b.voice_live = True
        class Native:
            async def exchange(inner, message, expected): return {'type': expected}
        self.b.native = Native()
        await self.b.controls(False, True)
        await self.b.speak_result('Do not play this later', self.b.interaction, 'turn')
        self.assertNotIn('thread/realtime/appendSpeech', self.calls)

    async def test_audio_state_changes_only_after_native_acknowledgment(self):
        from unittest.mock import patch
        entered, release = asyncio.Event(), asyncio.Event()
        class Native:
            async def exchange(inner, message, expected):
                self.assertEqual(message['controls'], {'microphoneMuted': True, 'speakerSuppressed': True})
                self.assertEqual(expected, 'audioControlsApplied')
                entered.set()
                await release.wait()
        self.b.native = Native()
        with patch.object(bridge, 'emit') as emit:
            task = asyncio.create_task(self.b.controls(True, True))
            await entered.wait()
            self.assertFalse(self.b.speaker_suppressed)
            emit.assert_not_called()
            release.set()
            await task
            self.assertTrue(self.b.speaker_suppressed)
            emit.assert_called_once_with('audioControls', muted=True, suppressed=True)

    async def test_audio_control_failure_ends_capture_before_reporting_retry(self):
        from unittest.mock import patch
        class Native:
            process = object()
            async def exchange(inner, message, expected): raise RuntimeError('rejected')
        self.b.native = Native()
        self.b.voice_live = True
        events = []
        async def terminate(process): events.append('capture ended')
        with patch.object(bridge, 'terminate', terminate), patch.object(bridge, 'emit') as emit:
            with self.assertRaisesRegex(RuntimeError, 'Choose Start session'):
                await self.b.command({'type': 'audioControls', 'muted': True, 'suppressed': True})
        self.assertEqual(events, ['capture ended'])
        self.assertIsNone(self.b.native)
        self.assertFalse(self.b.voice_live)
        self.assertTrue(self.b.stopped)
        self.assertFalse(any(c.args == ('audioControls',) for c in emit.call_args_list))

    async def test_late_audio_ack_cannot_change_a_replacement_session(self):
        from unittest.mock import patch
        class Native:
            async def exchange(inner, message, expected): self.b.native = object()
        self.b.native = Native()
        with patch.object(bridge, 'emit') as emit:
            await self.b.controls(True, True)
            emit.assert_not_called()
        self.assertFalse(self.b.speaker_suppressed)

    async def test_late_audio_failure_cannot_end_a_replacement_session(self):
        from unittest.mock import patch
        replacement = object()
        class Native:
            async def exchange(inner, message, expected):
                self.b.native = replacement
                raise RuntimeError('old helper closed')
        self.b.native = Native()
        self.b.voice_live = True
        with patch.object(bridge, 'emit') as emit:
            await self.b.command({'type': 'audioControls', 'muted': True, 'suppressed': True})
            emit.assert_not_called()
        self.assertIs(self.b.native, replacement)
        self.assertTrue(self.b.voice_live)

    async def test_native_audio_exit_is_reported_and_ends_voice(self):
        from unittest.mock import patch
        self.b.voice_live = True
        class Process:
            returncode = 17
            async def wait(inner): return 17
        class Native: process = Process()
        native = Native()
        self.b.native = native
        ended = []
        async def end(): ended.append(True)
        self.b.end_voice = end
        with patch.object(bridge, 'emit') as emit:
            await self.b.watch_native(native)
        self.assertTrue(ended)
        self.assertTrue(self.b.stopped)
        self.assertTrue(any(call.args[0] == 'failure' for call in emit.call_args_list))

    async def test_screenshot_reaches_worker_but_not_voice_text(self):
        original_ui = self.b.ui
        async def ui(operation, **params):
            result = await original_ui(operation, **params)
            if operation == 'observe':
                result['observation']['screenshot'] = 'data:image/jpeg;base64,TEST'
            return result
        self.b.ui = ui
        self.b.voice_live = True
        requests = []
        original_request = self.b.rpc.request
        async def request(method, params, **kwargs):
            requests.append((method, params))
            return await original_request(method, params, **kwargs)
        self.b.rpc.request = request
        await self.b.utterance('Click the visible button')
        await asyncio.gather(*self.b.tasks)
        worker = next(params for method, params in requests if method == 'turn/start')
        self.assertFalse(any(method == 'thread/realtime/appendText' for method, _ in requests))
        self.assertNotIn('base64', worker['input'][0]['text'])
        self.assertEqual(worker['input'][1], {'type': 'image', 'url': 'data:image/jpeg;base64,TEST'})

    async def test_desktop_observations_cannot_trigger_voice_narration(self):
        self.b.voice_live = True
        self.b.voice_thread = 'voice'
        await self.b.utterance('Click the visible button')
        if self.b.tasks: await asyncio.gather(*self.b.tasks)
        self.assertNotIn('thread/realtime/appendText', self.calls)

    async def test_tool_image_is_steered_before_releasing_tool_result(self):
        self.b.active_turn = 'turn'
        async def ui(operation, **params):
            return {'success': True, 'observation': {'epoch': 1, 'revision': 'fresh-revision', 'imageWidth': 240, 'imageHeight': 120, 'elements': [{'id': 0, 'AXTitle': 'Unique control label'}], 'screenshot': 'data:image/jpeg;base64,TEST'}}
        self.b.ui = ui
        requests = []
        original_request = self.b.rpc.request
        async def request(method, params, **kwargs):
            requests.append((method, params))
            return await original_request(method, params, **kwargs)
        self.b.rpc.request = request
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop', 'arguments': {'operation': 'observe'}}})
        result = self.calls[-1]['result']
        self.assertTrue(result['success'])
        self.assertNotIn('base64', result['contentItems'][0]['text'])
        self.assertEqual(len(result['contentItems']), 1)
        self.assertEqual(requests[0][0], 'turn/steer')
        self.assertEqual(requests[0][1]['expectedTurnId'], 'turn')
        # The full observation belongs only in the tool result, not both messages.
        self.assertNotIn('"observation"', requests[0][1]['input'][0]['text'])
        image_context = requests[0][1]['input'][0]['text']
        self.assertIn('"revision": "fresh-revision"', image_context)
        self.assertIn('"epoch": 1', image_context)
        self.assertIn('"imageWidth": 240', image_context)
        self.assertNotIn('Unique control label', image_context)
        self.assertIn('Unique control label', result['contentItems'][0]['text'])
        self.assertEqual(json.loads(result['contentItems'][0]['text'])['observation']['epoch'], 1)
        self.assertEqual(requests[0][1]['input'][1], {'type': 'image', 'url': 'data:image/jpeg;base64,TEST'})

    async def test_image_delivery_failure_stops_work(self):
        self.b.active_turn = 'turn'
        async def ui(operation, **params):
            self.calls.append(operation)
            return {'success': True, 'observation': {'screenshot': 'data:image/jpeg;base64,TEST'}}
        async def request(*args, **kwargs): raise RuntimeError('rejected')
        self.b.ui, self.b.rpc.request = ui, request
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop', 'arguments': {'operation': 'observe'}}})
        self.assertTrue(self.b.stopped)
        self.assertIn('stop', self.calls)
        self.assertFalse(self.calls[-1]['result']['success'])

    async def test_failed_desktop_action_is_visible_in_sidebar(self):
        from unittest.mock import patch
        self.b.active_turn = 'turn'
        async def ui(operation, **params):
            return {'success': False, 'error': 'The control rejected Press.'}
        self.b.ui = ui
        with patch.object(bridge, 'emit') as emit:
            await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn',
                'tool': 'desktop', 'arguments': {'operation': 'press', 'element': 0, 'epoch': 1, 'revision': 'fresh'}}})
        emit.assert_any_call('actionFailure', message='The control rejected Press.')

    async def test_invalid_arguments_reply_without_dispatch_or_ending_session(self):
        from unittest.mock import patch
        self.b.active_turn = 'turn'
        invalid = ['{"operation":', '[' * 1100 + '0' + ']' * 1100,
                   '[]', {'epoch': 1, 'revision': 'fresh'}]
        invalid += [{'operation': 'click', 'epoch': 1, 'revision': 'fresh',
                     'x': value, 'y': 2}
                    for value in (float('nan'), float('inf'), -float('inf'), 1 << 100)]
        invalid.append({'operation': 'click', 'epoch': 1 << 100,
                        'revision': 'fresh', 'x': 1, 'y': 2})
        with patch.object(bridge, 'emit'):
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.calls.clear()
                    await self.b.tool({'id': 7, 'params': {'threadId': 'worker',
                        'turnId': 'turn', 'tool': 'desktop', 'arguments': arguments}})
                    self.assertEqual(len(self.calls), 1)
                    self.assertFalse(self.calls[0]['result']['success'])
                    self.assertFalse(self.b.stopped)
                    self.assertEqual(self.b.active_turn, 'turn')
            self.calls.clear()
            await self.b.tool({'id': 8, 'params': {'threadId': 'worker',
                'turnId': 'turn', 'tool': 'desktop', 'arguments': '{"operation":"observe"}'}})
        self.assertEqual(self.calls[0], 'observe')
        self.assertTrue(self.calls[1]['result']['success'])

    async def test_completed_turn_cannot_be_reopened_by_delayed_started_event(self):
        from unittest.mock import patch
        self.b.active_turn = 'finished'
        self.b.turn_done.clear()
        with patch.object(bridge, 'emit'):
            await self.b.on_event({'method': 'turn/completed', 'params': {
                'threadId': 'worker', 'turn': {'id': 'finished', 'status': 'completed'}}})
            await self.b.on_event({'method': 'turn/started', 'params': {
                'threadId': 'worker', 'turn': {'id': 'finished'}}})
            self.assertIsNone(self.b.active_turn)
            self.assertTrue(self.b.turn_done.is_set())
            self.calls.clear()
            await self.b.tool({'id': 9, 'params': {'threadId': 'worker',
                'turnId': 'finished', 'tool': 'desktop', 'arguments': {'operation': 'observe'}}})
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0]['result']['success'])

    async def test_out_of_scope_operation_is_rejected(self):
        self.b.active_turn = 'turn'
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop', 'arguments': {'operation': 'shell', 'text': 'anything'}}})
        self.assertFalse(self.calls[-1]['result']['success'])

    async def test_missing_action_tokens_are_rejected_before_desktop_dispatch(self):
        self.b.active_turn = 'turn'
        for fields in ({}, {'epoch': 1}, {'epoch': 1, 'revision': ''}):
            self.calls.clear()
            await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn',
                'tool': 'desktop', 'arguments': {'operation': 'click', 'x': 2, 'y': 2, **fields}}})
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(self.calls[-1]['result']['success'])
            self.assertIn('latest screenshot', self.calls[-1]['result']['contentItems'][0]['text'])

    async def test_quoted_epoch_reports_type_error_without_dispatch(self):
        self.b.active_turn = 'turn'
        await self.b.tool({'id': 7, 'params': {'threadId': 'worker', 'turnId': 'turn', 'tool': 'desktop', 'arguments': {'operation': 'click', 'epoch': '1', 'revision': 'r', 'x': 2, 'y': 2}}})
        self.assertEqual(len(self.calls), 1)
        self.assertIn('epoch must be a JSON integer', self.calls[-1]['result']['contentItems'][0]['text'])

    async def test_correction_cancels_settles_observes_then_resumes(self):
        self.b.active_turn = 'old-turn'
        self.b.turn_done.clear()
        await self.b.utterance('Change the visible heading to Friday')
        self.assertEqual(self.calls, ['pause', 'turn/interrupt', 'observe', 'resume', 'turn/start'])
        self.assertIn(self.text, self.prompt)
        self.assertIn('Change the visible heading', self.prompt)

    async def test_stop_never_starts_a_new_turn(self):
        from unittest.mock import patch
        for text in ('Stop!', 'Stop session', 'End session'):
            self.calls.clear()
            with patch.object(bridge, 'emit') as emit:
                await self.b.utterance(text)
            self.assertEqual(self.calls, ['pause', 'stop'])
            self.assertTrue(self.b.stopped)
            emit.assert_any_call('sessionStopRequested')

    async def test_speech_cannot_reopen_explicit_stop(self):
        self.b.stopped = True
        await self.b.utterance('Actually change the title')
        self.assertEqual(self.calls, ['pause'])

    async def test_late_old_turn_tool_is_rejected(self):
        from unittest.mock import patch
        self.b.active_turn = 'new-turn'
        with patch.object(bridge, 'emit') as emit:
            await self.b.tool({'id': 4, 'params': {'threadId': 'worker', 'turnId': 'old-turn', 'tool': 'desktop', 'arguments': {'operation': 'append', 'text': 'late'}}})
        self.assertFalse(any(c.args[0] == 'actionFailure' for c in emit.call_args_list))
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0]['result']['success'])

    async def test_question_includes_fresh_state(self):
        await self.b.utterance('What is the heading now?')
        self.assertEqual(self.calls, ['pause', 'observe', 'resume', 'turn/start'])
        self.assertIn(self.text, self.prompt)

    async def test_cancellation_timeout_never_reopens_gui(self):
        self.b.active_turn = 'old-turn'
        async def failed_request(*args, **kwargs):
            raise TimeoutError('cancel timed out')
        self.b.rpc.request = failed_request
        with self.assertRaises(TimeoutError):
            await self.b.utterance('Correction')
        self.assertEqual(self.calls, ['pause'])


    async def test_completion_before_start_reply_does_not_resurrect_turn(self):
        async def completed_request(method, params, **kwargs):
            await self.b.on_event({'method': 'turn/started', 'params': {'threadId': 'worker', 'turn': {'id': 'fast-turn'}}})
            await self.b.on_event({'method': 'turn/completed', 'params': {'threadId': 'worker', 'turn': {'id': 'fast-turn', 'status': 'completed'}}})
            return {'turn': {'id': 'fast-turn'}}
        self.b.rpc.request = completed_request
        await self.b.utterance('What is the heading?')
        self.assertIsNone(self.b.active_turn)
        self.assertTrue(self.b.turn_done.is_set())

    async def test_stale_resume_cannot_reopen_a_later_local_stop(self):
        self.b.stopped = True
        self.blocked = True
        await self.b.utterance('Continue', explicit=True)
        self.assertEqual(self.calls, ['pause', 'observe', 'resume'])
        self.assertTrue(self.b.stopped)

    async def test_account_mismatch_stops_before_catalog_or_thread(self):
        async def account_request(method, params, **kwargs):
            self.calls.append(method)
            return {'account': {'type': 'chatgpt', 'email': 'different@example.test'}}
        self.b.rpc.request = account_request
        with self.assertRaisesRegex(RuntimeError, 'account changed'):
            await self.b.authenticate('expected@example.test')
        self.assertEqual(self.calls, ['account/read'])

    async def test_duplicate_canonical_transcript_only_submits_once(self):
        self.b.voice_live = True
        self.b.voice_thread = 'voice'
        self.b.session = 'session'
        calls = []
        async def utterance(text): calls.append(text)
        self.b.utterance = utterance
        event = {'method': 'thread/realtime/item/completed', 'params': {'threadId': 'voice', 'item': {
            'type': 'transcriptSegment', 'id': 'item', 'realtimeSessionId': 'session', 'role': 'user', 'text': 'Make a checklist'}}}
        await self.b.on_event(event)
        await self.b.on_event(event)
        await asyncio.gather(*self.b.tasks)
        self.assertEqual(calls, ['Make a checklist'])

    async def test_first_transcript_waits_for_native_startup(self):
        self.b.voice_thread = 'voice'
        self.b.session = 'session'
        self.b.stopped = True
        calls = []
        async def utterance(text): calls.append((text, self.b.stopped))
        self.b.utterance = utterance
        async with self.b.voice_lock:
            await self.b.on_event({'method': 'thread/realtime/item/completed', 'params': {
                'threadId': 'voice', 'item': {'type': 'transcriptSegment', 'id': 'first',
                'realtimeSessionId': 'session', 'role': 'user', 'text': 'Type hello'}}})
            await asyncio.sleep(0)
            self.assertEqual(calls, [])
            self.b.voice_live = True
            self.b.stopped = False
        await asyncio.gather(*self.b.tasks)
        self.assertEqual(calls, [('Type hello', False)])

    async def test_queued_transcript_from_ended_session_cannot_start_work(self):
        self.b.voice_live = True
        self.b.voice_thread = 'voice'
        self.b.session = 'session'
        calls = []
        async def utterance(text): calls.append(text)
        self.b.utterance = utterance
        async with self.b.voice_lock:
            await self.b.on_event({'method': 'thread/realtime/item/completed', 'params': {
                'threadId': 'voice', 'item': {'type': 'transcriptSegment', 'id': 'first',
                'realtimeSessionId': 'session', 'role': 'user', 'text': 'Type hello'}}})
            await asyncio.sleep(0)
            self.b.voice_thread = 'new-voice'
            self.b.session = 'new-session'
        await asyncio.gather(*self.b.tasks)
        self.assertEqual(calls, [])

    async def test_end_voice_stops_capture_before_waiting_for_cancellation(self):
        from unittest.mock import patch
        events = []
        class Native: process = object()
        self.b.native = Native()
        async def terminate(process): events.append('capture terminated')
        async def cancel(): events.append('worker cancellation requested')
        self.b.cancel_worker = cancel
        with patch.object(bridge, 'terminate', terminate):
            await self.b.end_voice()
        self.assertEqual(events, ['capture terminated', 'worker cancellation requested'])

    async def test_disconnect_acknowledgment_waits_for_audio_and_worker_cleanup(self):
        from unittest.mock import patch
        events = []
        self.b.voice_live = True
        self.b.voice_thread = 'voice'
        class Native: process = object()
        self.b.native = Native()
        async def terminate(process): events.append('audio ended')
        async def cancel(): events.append('worker settled')
        def emit(kind, **fields):
            if kind == 'connection': events.append(fields['state'])
        self.b.cancel_worker = cancel
        with patch.object(bridge, 'terminate', terminate), patch.object(bridge, 'emit', emit):
            await self.b.end_voice()
        self.assertEqual(events, ['disconnecting', 'audio ended', 'worker settled', 'disconnected'])
        self.assertIsNone(self.b.native)
        self.assertIsNone(self.b.voice_thread)

    async def test_ending_an_idle_session_acknowledges_disconnected(self):
        from unittest.mock import patch
        with patch.object(bridge, 'emit') as emit:
            await self.b.end_voice()
        emit.assert_called_once_with('connection', state='disconnected')

    async def test_request_feedback_precedes_slow_screen_capture(self):
        from unittest.mock import patch
        events = []
        original_ui = self.b.ui
        async def ui(operation, **params):
            if operation == 'observe': events.append('capture')
            return await original_ui(operation, **params)
        def emit(kind, **fields):
            if fields.get('message') == 'Request received. Preparing the next step…': events.append('received')
        self.b.ui = ui
        with patch.object(bridge, 'emit', emit): await self.b.utterance('Type hello')
        self.assertEqual(events, ['received', 'capture'])

    async def test_request_feedback_does_not_wait_for_prior_worker_cancellation(self):
        from unittest.mock import patch
        received, cancel_started, release = (asyncio.Event() for _ in range(3))
        async def cancel():
            cancel_started.set()
            await release.wait()
        def emit(kind, **fields):
            if fields.get('message') == 'Request received. Preparing the next step…': received.set()
        self.b.cancel_worker = cancel
        with patch.object(bridge, 'emit', emit):
            task = asyncio.create_task(self.b.utterance('Change the heading'))
            try:
                await asyncio.wait_for(cancel_started.wait(), 1)
                self.assertTrue(received.is_set())
                self.assertNotIn('turn/start', self.calls)
            finally:
                release.set()
                await task


    async def test_request_received_while_stopped_is_preserved_for_resume(self):
        self.b.stopped = True
        await self.b.utterance('Add a picnic checklist')
        self.assertEqual(self.b.last_task, 'Add a picnic checklist')
        self.assertNotIn('turn/start', self.calls)
        self.calls.clear()
        await self.b.command({'type': 'resume'})
        self.assertIn('Add a picnic checklist', self.prompt)
        self.assertEqual(self.b.last_task, 'Add a picnic checklist')

    async def test_resume_without_a_received_task_does_not_invent_one(self):
        await self.b.command({'type': 'resume'})
        self.assertNotIn('turn/start', self.calls)


class EnvironmentTests(unittest.TestCase):
    def test_api_and_loader_keys_are_not_inherited(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test', 'DYLD_INSERT_LIBRARIES': '/tmp/test', 'GST_PLUGIN_PATH': '/tmp/test'}):
            environment = bridge.clean_environment()
            self.assertNotIn('OPENAI_API_KEY', environment)
            self.assertNotIn('DYLD_INSERT_LIBRARIES', environment)
            self.assertEqual(environment['GST_PLUGIN_PATH'], '')


if __name__ == '__main__':
    unittest.main()
