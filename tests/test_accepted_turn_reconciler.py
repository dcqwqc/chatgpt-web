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
            db.execute("CREATE TABLE events (thread_id TEXT,sequence INTEGER,type TEXT,data TEXT,created_at INTEGER)")
            db.execute("INSERT INTO threads VALUES (?,?,?)",(
                self.tid, "acp-chatgpt-web", "error"))
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (self.tid,1,
                "client/turn/requested",json.dumps({
                    "source":"spawn","initiator":"user","requestId":"req1",
                    "input":[{"type":"text","text":self.prompt}]}),1760000000000))
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

    def prepare_legacy(self):
        s=json.loads(self.session_file.read_text())
        s.pop("pending_turn")
        s["conversation_id"]=self.cid
        self.session_file.write_text(json.dumps(s))
        self.document["messages"][0]["create_time"]=1760000000
        with sqlite3.connect(self.bb / "bb.db") as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)",(
                self.tid,2,"provider/error",
                json.dumps({"detail":"peer closed connection (incomplete chunked read)"}),
                1760000010000))

    def test_legacy_transport_failure_recovers_exact_original_only(self):
        self.prepare_legacy()
        calls=[]
        def fake_run(*args,**kwargs):
            calls.append(args[1:])
            if args[1]=="show":
                return SimpleNamespace(returncode=0, stdout=json.dumps(self.document))
            return SimpleNamespace(returncode=0, stdout='{"ok":true}')
        with patch.object(reconciler, "run", side_effect=fake_run):
            report=reconciler.reconcile_once()
        self.assertEqual(report,[{"thread":self.tid,"status":"dispatched"}])
        self.assertEqual([x[0] for x in calls],["show","show","thread","thread"])
        saved=json.loads(self.session_file.read_text())
        self.assertEqual(saved["pending_turn"]["message_id"],self.mid)

    def test_legacy_incomplete_source_does_not_modify_session(self):
        self.prepare_legacy()
        self.document["messages"][1]["status"]="in_progress"
        original=self.session_file.read_bytes()
        with patch.object(reconciler, "run",
                          return_value=SimpleNamespace(returncode=0,stdout=json.dumps(self.document))) as run:
            self.assertEqual(reconciler.reconcile_once(),[])
            run.assert_called_once()
        self.assertEqual(self.session_file.read_bytes(),original)

    def test_legacy_wrong_user_identity_rejected(self):
        self.prepare_legacy()
        self.document["messages"][0]["parts"]=["a completely different request"]
        original=self.session_file.read_bytes()
        with patch.object(reconciler, "run",
                          return_value=SimpleNamespace(returncode=0,stdout=json.dumps(self.document))):
            self.assertEqual(reconciler.reconcile_once(),[])
        self.assertEqual(self.session_file.read_bytes(),original)

    def test_legacy_other_provider_error_rejected(self):
        self.prepare_legacy()
        with sqlite3.connect(self.bb/"bb.db") as db:
            db.execute("UPDATE events SET data=? WHERE type='provider/error'",
                       (json.dumps({"detail":"permission denied"}),))
        with patch.object(reconciler, "run") as run:
            self.assertEqual(reconciler.reconcile_once(),[])
            run.assert_not_called()

    def test_unrelated_thread_and_malformed_sessions_are_skipped(self):
        with sqlite3.connect(self.bb / "bb.db") as db:
            db.execute("UPDATE threads SET provider_id='acp-other'")
        with patch.object(reconciler,"run") as run:
            self.assertEqual(reconciler.reconcile_once(),[])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
