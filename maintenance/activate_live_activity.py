#!/usr/bin/env python3
"""One-shot idle-only BB ChatGPT Web adapter activation and health check.

A running BB ChatGPT Web turn executes through this Python ACP process.
Restarting it before the current turn completes destroys the reply. This
one-shot waits for durable completion and an empty CLI send process set.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

BB = Path.home() / '.bb'
THREAD = 'thr_tqgb78fdsw'
STATE = Path.home() / '.local/state/chatgpt-web/live-activity-activation.json'
SRC = Path.home() / '.local/opt/chatgpt-web-fork'


def call(*args, timeout=35):
    return subprocess.run(args, text=True, capture_output=True,
                          timeout=timeout, check=False)


def log(stage, detail=''):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {'stage': stage, 'detail': detail, 'time': time.time()}
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2) + '\n')
    os.chmod(temp, 0o600)
    temp.replace(STATE)
    print(f'{stage}: {detail}', flush=True)


def current_completed():
    try:
        with sqlite3.connect(f'file:{BB / "bb.db"}?mode=ro', uri=True, timeout=2) as db:
            row = db.execute('''select turn_id from events
                where thread_id = ? and type = 'turn/started'
                order by created_at desc, sequence desc limit 1''', (THREAD,)).fetchone()
            if row is None:
                return False
            completed = db.execute('''select 1 from events
                where thread_id = ? and turn_id = ?
                and type in ('turn/completed', 'turn/failed', 'turn/cancelled')
                limit 1''', (THREAD, row[0])).fetchone()
        return bool(completed)
    except sqlite3.Error:
        return False


def active_sends():
    result = call('ps', '-eo', 'args=', timeout=8)
    if result.returncode:
        return True
    return any('chatgpt-web send ' in line and '/bin/python' in line
               for line in result.stdout.splitlines())


def ready():
    if not current_completed() or active_sends():
        return False
    # Avoid resetting the worker immediately after its process exits.
    time.sleep(5)
    return current_completed() and not active_sends()



def post_restart_smoke() -> tuple[bool, str]:
    """Run an isolated hidden request; verify reply and early BB activity."""
    bb = str(Path.home() / '.local/bin/bb')
    spawned = call(bb, 'thread', 'spawn', '--project', 'proj_personal',
                   '--provider', 'acp-chatgpt-web', '--model', 'gpt-6',
                   '--reasoning-level', 'instant', '--new-environment', 'personal',
                   '--visibility', 'hidden', '--title', 'ChatGPT Web reload smoke',
                   '--prompt', 'Reply exactly BB-STREAM-RELOAD-OK-20261108.',
                   '--json', timeout=30)
    if spawned.returncode:
        return False, 'Hidden smoke request could not start'
    try:
        thread = json.loads(spawned.stdout)['id']
    except (ValueError, KeyError, TypeError):
        return False, 'Hidden smoke request returned no thread ID'
    try:
        waited = call(bb, 'thread', 'wait', thread, '--event', 'turn/completed',
                      '--timeout', '100s', '--json', timeout=112)
        if waited.returncode:
            return False, 'Hidden smoke response did not complete'
        # BB can acknowledge turn/completed before its output and activity
        # projections are consistently visible. Re-read evidence; NEVER
        # resend the accepted ChatGPT prompt to repair a read-side race.
        for attempt in range(10):
            output = call(bb, 'thread', 'output', thread, '--json', timeout=15)
            events = call(bb, 'thread', 'log', thread, '--json',
                          '--limit', '100', timeout=15)
            if output.returncode == 0 and events.returncode == 0:
                answer = json.loads(output.stdout).get('output')
                rows = json.loads(events.stdout)
                starts = [e for e in rows if e.get('type') == 'item/started']
                has_activity = any((e.get('data', {}).get('item', {}).get('type')
                                    in ('reasoning', 'toolCall')) for e in starts)
                if answer == 'BB-STREAM-RELOAD-OK-20261108.' and has_activity:
                    return True, 'Hidden BB answer and activity rows verified'
                # The current ChatGPT model may omit the final period while
                # following an exact-token instruction. Compare the marker,
                # not its optional punctuation.
                if (isinstance(answer, str) and
                        answer.strip().rstrip('.') == 'BB-STREAM-RELOAD-OK-20261108'
                        and has_activity):
                    return True, 'Hidden BB answer and activity rows verified'
            if attempt < 9:
                time.sleep(0.4)
        return False, 'Hidden smoke missing final answer or live activity after reconciliation'
    except (ValueError, TypeError, KeyError):
        return False, 'Hidden smoke verification returned invalid data'
    finally:
        call(bb, 'thread', 'archive', thread, '--json', timeout=15)

def main():
    log('waiting-for-idle', 'Preserving the active BB response')
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if ready():
            break
        time.sleep(3)
    else:
        log('deferred', 'No verified idle window within 15 minutes; no restart made')
        return 2
    # Release only this BB thread's loaded ACP runtime. Reloading provider-acp
    # globally would also disturb unrelated ACP providers and active projects.
    log('releasing-thread-runtime', 'Stopping the completed BB turn safely')
    stop = call(str(Path.home() / '.local/bin/bb'), 'thread', 'stop', THREAD,
                '--json', timeout=35)
    if stop.returncode:
        log('failed', 'Scoped BB thread stop failed: ' + (stop.stderr or stop.stdout).strip()[:240])
        return 3
    if active_sends():
        log('deferred', 'New send began after release; daemon restart deferred')
        return 5
    log('reloading-daemon', 'Restarting ChatGPT Web local service')
    restart = call('systemctl', '--user', 'restart', 'chatgpt-web.service', timeout=50)
    if restart.returncode:
        log('failed', 'ChatGPT Web service restart failed: ' + restart.stderr.strip()[:240])
        return 3
    status = call(str(Path.home() / '.local/bin/chatgpt-web'), 'status', timeout=25)
    if status.returncode:
        log('failed', 'Daemon failed status check: ' + status.stderr.strip()[:240])
        return 4
    # No global plugin reload: the next BB request creates a new ACP process
    # from the updated source, preserving other providers and loaded sessions.
    models = call(str(Path.home() / '.local/bin/bb'), 'provider', 'models', 'acp-chatgpt-web', '--json', timeout=35)
    if models.returncode:
        log('failed', 'Provider models check failed: ' + (models.stderr or models.stdout).strip()[:240])
        return 7
    log('smoke-check', 'Testing fresh hidden BB ChatGPT response and progress')
    healthy, detail = post_restart_smoke()
    if not healthy:
        log('failed', detail)
        return 8
    log('activated', 'Daemon and ACP reloaded; catalog reachable; ' + detail)
    return 0


if __name__ == '__main__':
    sys.exit(main())
