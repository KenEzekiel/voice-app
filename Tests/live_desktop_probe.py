#!/usr/bin/env python3
"""Opt-in subscription-backed screenshot/tool test. Synthetic screen, no real GUI input."""
import argparse
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import struct
import zlib

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).resolve().parents[1] / 'Sources/bridge.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def screenshot(changed, left_changed=False):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    rows = []
    for y in range(120):
        row = bytearray([0])
        for x in range(240):
            color = (245, 245, 245)
            if 20 <= x < 90 and 25 <= y < 95: color = (240, 200, 20) if left_changed else (230, 20, 20)
            if 150 <= x < 220 and 25 <= y < 95: color = (20, 200, 40) if changed else (20, 40, 230)
            row.extend(color)
        rows.append(bytes(row))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 240, 120, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(png).decode()


async def probe(account):
    b = module.Bridge()
    state = {'changed': False, 'epoch': 0, 'revision': 0}
    actions, reports, calls, tool_results = [], [], [], []
    def observation():
        state['revision'] += 1
        return {'app': 'Synthetic visual fixture', 'epoch': state['epoch'],
                'revision': str(state['revision']), 'imageWidth': 240, 'imageHeight': 120,
                'text': 'Synthetic screenshot. Determine appearance from the image, not this text.',
                'screenshot': screenshot(state['changed'])}
    async def ui(operation, **params):
        calls.append({'operation': operation, **params})
        if operation in ('pause', 'stop'):
            state['epoch'] += 1
            return {'success': True, 'epoch': state['epoch']}
        if operation == 'resume': return {'success': params.get('epoch') == state['epoch']}
        if operation != 'observe':
            if params.get('epoch') != state['epoch'] or params.get('revision') != str(state['revision']):
                return {'success': False, 'error': 'stale observation'}
            if operation != 'click' or not (150 <= params.get('x', -1) < 220 and 25 <= params.get('y', -1) < 95):
                return {'success': False, 'error': 'Only the requested visible target can be clicked in this fixture'}
            state['changed'] = True
            actions.append({'operation': operation, 'x': params['x'], 'y': params['y']})
        return {'success': True, 'observation': observation()}
    b.ui = ui
    original = b.on_event
    async def on_event(message):
        item = message.get('params', {}).get('item', {})
        if item.get('type') == 'dynamicToolCall':
            tool_results.append({'status': item.get('status'), 'success': item.get('success'),
                                 'contentTypes': [part.get('type') for part in (item.get('contentItems') or [])]})
        if item.get('type') == 'agentMessage' and item.get('phase') != 'commentary': reports.append(item.get('text', ''))
        await original(message)
    b.on_event = on_event
    try:
        await b.boot()
        await b.authenticate(account)
        b.worker_thread = await b.start_thread(module.WORKER_INSTRUCTIONS, [module.TOOL])
        b.stopped = False
        await b.utterance('This is a disposable simulated GUI test. Click the blue square once, then report what color that square becomes. Inspect the returned image; do not infer success from dispatch.')
        await asyncio.wait_for(b.turn_done.wait(), 90)
        initial_reports = list(reports)
        passed = len(actions) == 1 and any('green' in text.lower() for text in reports)
        if not passed:
            await b.utterance('Observation-only diagnostic: describe the colors of the left square and right square in the fresh screenshot attached to this message. Do not perform actions.')
            await asyncio.wait_for(b.turn_done.wait(), 90)
            result = {'passed': False, 'calls': calls, 'toolResults': tool_results,
                      'postActionReports': initial_reports, 'directImageReports': reports[len(initial_reports):],
                      'proof': 'Synthetic screenshot routing test; no real desktop or input.'}
            return result
        return {'proof': 'Real subscription-backed Codex inference using synthetic screenshots and a simulated click. No actual desktop capture, microphone, or OS input.',
                'passed': True, 'visualTargetSelected': True, 'postActionImageUnderstood': True,
                'actions': actions, 'reports': reports, 'actualGUIAcceptance': False}
    finally:
        await b.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.account))
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)
