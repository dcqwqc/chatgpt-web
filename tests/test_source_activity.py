"""Provenance, timestamp and privacy guarantees of read-only source status."""
import unittest
from maintenance.source_activity import observe_source


class SourceActivityTests(unittest.TestCase):
    def setUp(self):
        self.doc = {"id": "cid", "messages": [
            {"id": "mid", "role": "user", "status": "finished_successfully",
             "parts": ["policy\nmy prompt"], "create_time": 1794131000},
            {"id": "a1", "role": "assistant", "recipient": "functions.exec",
             "channel": "commentary", "status": "finished_successfully",
             "create_time": 1794132000,
             "parts": ["secret tokens should not appear"]},
            {"id": "t1", "role": "tool", "recipient": "all",
             "create_time": 1794132010, "parts": ["secret password"]},
        ]}

    def get(self):
        return observe_source(self.doc, conversation_id="cid",
                              message_id="mid", user_prompt="my prompt",
                              now=1794136000)

    def test_verified_activity_is_source_timed_not_thinking(self):
        o=self.get()
        self.assertEqual(o["last_activity"],1794132010)
        self.assertEqual(o["tool_calls"],1)
        self.assertFalse(o["has_final"])
        self.assertGreater(o["age_seconds"],360)
        self.assertIn("exec",o["detail"])
        self.assertNotIn("password",o["detail"])
        self.assertNotIn("secret",o["detail"])

    def test_different_original_message_rejected(self):
        self.doc["messages"][0]["id"]="wrong"
        self.assertIsNone(self.get())

    def test_superseded_turn_rejected(self):
        self.doc["messages"].append({"id":"new","role":"user","parts":["new"]})
        self.assertIsNone(self.get())

    def test_successful_final_is_observed_not_inferred(self):
        self.doc["messages"].append({
            "role":"assistant","channel":"final",
            "status":"finished_successfully", "recipient":"all",
            "text":"Verified finished answer","create_time":1794132100})
        self.assertTrue(self.get()["has_final"])


if __name__ == "__main__":
    unittest.main()
