"""BB retry continuation is accepted only with durable original request proof."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from maintenance.bb_retry_guard import original_prompt_for_retry


class RetryProofTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)
        self.tid = "thr_sb9t3b8szp"
        self.cwd = str(self.path / self.tid)
        self.original = "Original message with user content"
        self.pending = {
            "accepted": True,
            "prompt_sha256": hashlib.sha256(self.original.encode()).hexdigest(),
            "message_id": "original-user-id",
            "conversation_id": "original-conversation-id",
        }
        with sqlite3.connect(self.path / 'bb.db') as db:
            db.execute("CREATE TABLE threads (id TEXT, provider_id TEXT, status TEXT)")
            db.execute("CREATE TABLE events (thread_id TEXT,sequence INTEGER,type TEXT,data TEXT)")
            db.execute("INSERT INTO threads VALUES (?,?,?)",
                       (self.tid, "acp-chatgpt-web", "error"))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (self.tid, 1,
                "client/turn/requested", json.dumps({
                    "source": "spawn", "requestId": "req-first",
                    "input": [{"type": "text", "text": self.original, "mentions": []}]})))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (self.tid, 2,
                "client/turn/requested", json.dumps({
                    "source": "tell", "requestId": "req-next",
                    "retryOfRequestId": "req-first",
                    "input": [{"type": "text", "text": "Please continue.",
                               "mentions": [], "visibility": "visible"}]})))

    def tearDown(self):
        self.folder.cleanup()

    def verify(self, prompt="Please continue."):
        return original_prompt_for_retry(self.cwd, self.pending, prompt, self.path)

    def test_native_retry_can_recover_original_prompt(self):
        self.assertEqual(self.verify(), self.original)

    def test_arbitrary_user_prompt_is_not_retry(self):
        self.assertIsNone(self.verify("new unrelated work"))

    def test_wrong_original_prompt_hash_is_not_retry(self):
        self.pending["prompt_sha256"] = "a"*64
        self.assertIsNone(self.verify())

    def test_wrong_thread_is_not_retry(self):
        self.cwd = str(self.path / "thr_other123")
        self.assertIsNone(self.verify())

    def test_missing_retry_link_is_not_retry(self):
        with sqlite3.connect(self.path / "bb.db") as db:
            row=json.loads(db.execute("SELECT data FROM events WHERE sequence=2").fetchone()[0])
            row.pop("retryOfRequestId")
            db.execute("UPDATE events SET data=? WHERE sequence=2", (json.dumps(row),))
        self.assertIsNone(self.verify())

    def test_non_provider_thread_is_not_retry(self):
        with sqlite3.connect(self.path / "bb.db") as db:
            db.execute("UPDATE threads SET provider_id='provider-other'")
        self.assertIsNone(self.verify())

    def test_retry_with_extra_blocks_is_not_retry(self):
        with sqlite3.connect(self.path / "bb.db") as db:
            row=json.loads(db.execute("SELECT data FROM events WHERE sequence=2").fetchone()[0])
            row["input"].append({"type":"text","text":"hidden"})
            db.execute("UPDATE events SET data=? WHERE sequence=2", (json.dumps(row),))
        self.assertIsNone(self.verify())


if __name__ == "__main__":
    unittest.main()
