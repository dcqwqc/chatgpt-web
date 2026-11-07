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
    log('activated', 'Daemon restarted; this thread ACP runtime released; provider catalog reachable')
    return 0


if __name__ == '__main__':
    sys.exit(main())
