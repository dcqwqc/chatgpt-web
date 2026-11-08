"""Proof that the background self-healer never resends a ChatGPT prompt."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from maintenance import accepted_turn_reconciler as reconciler


class AcceptedTurnReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bb = self.home / ".bb"
        self.sessions = self.home / "sessions"
        self.state = self.home / "reconciler"
        self.bb.mkdir()
        self.sessions.mkdir()
        self.tid = "thr_abcdef123"
        self.sid = "a" * 32
        self.cid = "6af03ffb-8920-83eb-a2cc-8ceaf959bf9c"
        self.mid = "user-accepted-id"
        self.prompt = "My exact BB request"
        self.pending = {
            "accepted": True, "message_id": self.mid,
            "conversation_id": self.cid,
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest()}
        self.session_file = self.sessions / (self.sid + ".json")
        self.session_file.write_text(json.dumps({
            "sessionId": self.sid, "cwd": str(self.bb / "workspaces" / self.tid),
            "pending_turn": self.pending
        }))
        with sqlite3.connect(self.bb / "bb.db") as db:
            db.execute("CREATE TABLE threads (id TEXT, provider_id TEXT, status TEXT)")
            db.execute("CREATE TABLE events (thread_id TEXT,sequence INTEGER,type TEXT,data TEXT)")
            db.execute("INSERT INTO threads VALUES (?,?,?)",(
                self.tid, "acp-chatgpt-web", "error"))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (self.tid,1,
                "client/turn/requested",json.dumps({
                    "source":"spawn","requestId":"req1",
                    "input":[{"type":"text","text":self.prompt}]})))
        self.document = {"id":self.cid,"messages":[
            {"role":"user","id":self.mid,"status":"finished_successfully",
             "parts":["system\n"+self.prompt]},
            {"role":"assistant","channel":"final","recipient":"all",
             "status":"finished_successfully","text":"Original saved answer"}]}
        self.patches=[
            patch.object(reconciler,"BB",self.bb),
            patch.object(reconciler,"SESSIONS",self.sessions),
            patch.object(reconciler,"STATE",self.state),
            patch.object(reconciler,"CLI",self.home/"chatgpt-web"),
            patch.object(reconciler,"BB_CLI",self.home/"bb"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_completed_original_answer_retries_projection_only(self):
        calls=[]
        def fake_run(*args,**kwargs):
            calls.append(tuple(args))
            if args[1] == "show":
                return SimpleNamespace(returncode=0,stdout=json.dumps(self.document))
            return SimpleNamespace(returncode=0,stdout='{"ok":true}')
        with patch.object(reconciler,"run",side_effect=fake_run):
            report=reconciler.reconcile_once()
        self.assertEqual(report,[{"thread":self.tid,"status":"dispatched"}])
        self.assertEqual([x[1] for x in calls],["show","thread","thread"])
        self.assertEqual([x[2] for x in calls if x[1]=="thread"],["stop","retry"])
        self.assertEqual(reconciler.reconcile_once.__name__,"reconcile_once")
        # Bounded and persisted; never continuously resubmit.
        with patch.object(reconciler,"run",side_effect=fake_run):
            self.assertEqual(reconciler.reconcile_once(),[])
        self.assertEqual(len(calls),3)

    def test_unfinished_web_response_never_retries(self):
        self.document["messages"][1]["status"]="in_progress"
        calls=[]
        def fake_run(*args,**kwargs):
            calls.append(tuple(args))
            return SimpleNamespace(returncode=0,stdout=json.dumps(self.document))
        with patch.object(reconciler,"run",side_effect=fake_run):
            self.assertEqual(reconciler.reconcile_once(),[])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1],"show")
        self.assertEqual(list(self.state.glob("*.json")),[])

    def test_unrelated_thread_and_malformed_sessions_are_skipped(self):
        with sqlite3.connect(self.bb / "bb.db") as db:
            db.execute("UPDATE threads SET provider_id='acp-other'")
        with patch.object(reconciler,"run") as run:
            self.assertEqual(reconciler.reconcile_once(),[])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
