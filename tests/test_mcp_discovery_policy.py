import unittest
from ops.mcp_discovery_policy import updated_policy


class DiscoveryPolicyTests(unittest.TestCase):
    def test_only_read_only_aliases_are_added_idempotently(self):
        original = {"example-terminal": {"tools": ["run_command"]},
                    "chatgpt": {"tools": ["existing"], "note": "preserve"}}
        result = updated_policy(original)
        self.assertEqual(result["example-terminal"], original["example-terminal"])
        self.assertEqual(original["chatgpt"]["tools"], ["existing"])
        self.assertEqual(result["chatgpt"], {"tools": ["existing", "find_mcp_tools",
            "get_mcp_tool_schema"], "note": "preserve"})
        self.assertEqual(updated_policy(result), result)
