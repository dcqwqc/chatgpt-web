"""Recovery must never mistake an unfinished or unrelated response for success."""
import unittest
from maintenance.verified_recovery import verified_final


class VerifiedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conversation = {
            "id": "conv-123", "messages": [
                {"id": "user-123", "role": "user", "status": "finished_successfully",
                 "parts": ["policy header\n\nExactly my prompt"]},
                {"id": "comment", "role": "assistant", "channel": "commentary",
                 "status": "finished_successfully", "text": "Investigating"},
                {"id": "answer", "role": "assistant", "channel": "final",
                 "recipient": "all", "status": "finished_successfully",
                 "text": "Verified response"},
            ]
        }

    def check(self, **overrides):
        return verified_final(self.conversation, conversation_id=overrides.get(
            "conversation_id", "conv-123"), message_id=overrides.get(
            "message_id", "user-123"), user_prompt=overrides.get(
            "user_prompt", "Exactly my prompt"))

    def test_verified_final(self):
        self.assertEqual(self.check(), "Verified response")

    def test_reject_wrong_id_or_prompt_or_conversation(self):
        self.assertIsNone(self.check(message_id="user-other"))
        self.assertIsNone(self.check(conversation_id="conv-other"))
        self.assertIsNone(self.check(user_prompt="different request"))

    def test_reject_unfinished_draft_or_commentary(self):
        self.conversation["messages"][2]["status"] = "in_progress"
        self.assertIsNone(self.check())
        self.conversation["messages"].pop()
        self.assertIsNone(self.check())

    def test_reject_later_user_and_tool_answer(self):
        self.conversation["messages"].insert(2, {
            "id": "another", "role": "user", "status": "finished_successfully",
            "parts": ["New prompt"]})
        self.assertIsNone(self.check())
        self.conversation["messages"].pop(2)
        self.conversation["messages"][2]["recipient"] = "python"
        self.assertIsNone(self.check())

    def test_reject_duplicate_message_id(self):
        self.conversation["messages"].insert(1, self.conversation["messages"][0].copy())
        self.assertIsNone(self.check())

    def test_reject_empty_final(self):
        self.conversation["messages"][2]["text"] = ""
        self.assertIsNone(self.check())


if __name__ == "__main__":
    unittest.main()
