"""Readable, stable, evidence-based BB activity labels."""
import unittest
from bb_acp import readable_tool_activity


class ToolFidelityTests(unittest.TestCase):
    def test_familiar_tools_have_readable_labels(self):
        self.assertEqual(readable_tool_activity('functions.exec', '')[0], 'Run connected tools')
        self.assertEqual(readable_tool_activity('api_tool.call_tool', '{"server":"philipedia-terminal","tool":"run_command"}')[0],
                         'Use connected app · run_command')
        self.assertEqual(readable_tool_activity('web.run', '')[0], 'Search and retrieve web data')
        self.assertEqual(readable_tool_activity('mcp__Loom__loom_task_update', '')[0], 'Loom · loom_task_update')

    def test_unknown_argument_format_does_not_invent_operation(self):
        self.assertEqual(readable_tool_activity('api_tool.call_tool', '{broken')[0], 'Use connected app')
        self.assertEqual(readable_tool_activity('functions.exec', '{"command":"git status"}')[0],
                         'Run connected tools · git status')
        self.assertEqual(readable_tool_activity('functions.exec', '{"command":"a" * 200}')[0],
                         'Run connected tools')

if __name__ == '__main__':
    unittest.main()
