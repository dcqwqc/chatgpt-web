"""Readable, stable, evidence-based BB activity labels."""
import unittest
from bb_acp import readable_tool_activity, concise_tool_details, safe_tool_input


class ToolFidelityTests(unittest.TestCase):
    def test_familiar_tools_have_readable_labels(self):
        self.assertEqual(readable_tool_activity('functions.exec', '')[0], 'Run connected tools')
        self.assertEqual(readable_tool_activity('api_tool.call_tool', '{"server":"philipedia-terminal","tool":"run_command"}')[0],
                         'Use connected app · run_command')
        self.assertEqual(readable_tool_activity('web.run', '')[0], 'Search and retrieve web data')
        self.assertEqual(readable_tool_activity('mcp__Loom__loom_task_update', '')[0], 'Loom · loom_task_update')

    def test_tool_input_is_structured_for_bb_and_secrets_are_redacted(self):
        payload = ('{"path":"/Philipedia Terminal/link_abc/run_command",'
                   '"args":{"host":"philipedia","command":"date +%H:%M",'
                   '"headers":{"Authorization":"Bearer sampletoken123456789",'
                   '"x-api-key":"privatevalue"}}}')
        result = safe_tool_input(payload)
        self.assertEqual(result["args"]["command"], "date +%H:%M")
        self.assertEqual(result["args"]["headers"]["Authorization"], "[redacted]")
        self.assertEqual(result["args"]["headers"]["x-api-key"], "[redacted]")
        self.assertEqual(safe_tool_input(""), None)
        self.assertEqual(safe_tool_input("some text"), {"input": "some text"})

    def test_short_safe_command_is_visible_even_without_expandable_arguments(self):
        payload = '{"path":"/Philipedia Terminal/link_123/run_command","args":{"host":"philipedia","command":"date +%H:%M"}}'
        self.assertEqual(concise_tool_details(payload), 'philipedia · date +%H:%M')
        self.assertEqual(concise_tool_details('{"args":{"host":"philipedia","command":"curl -H Authorization:Bearer secret"}}'), 'philipedia')
        self.assertEqual(concise_tool_details('{"args":{"host":"philipedia","command":"one\\ntwo"}}'), 'philipedia')

    def test_real_connected_app_envelope_preserves_specific_method(self):
        payload = '{"path":"/Philipedia Terminal/link_abcd1234/run_command","args":{"host":"philipedia","command":"git status"}}'
        label, actual = readable_tool_activity('api_tool.call_tool', payload)
        self.assertEqual(label, 'Philipedia Terminal · run command')
        self.assertEqual(actual, payload)

    def test_unknown_argument_format_does_not_invent_operation(self):
        self.assertEqual(readable_tool_activity('api_tool.call_tool', '{broken')[0], 'Use connected app')
        self.assertEqual(readable_tool_activity('functions.exec', '{"command":"git status"}')[0],
                         'Run connected tools · git status')
        self.assertEqual(readable_tool_activity('functions.exec', '{"command":"a" * 200}')[0],
                         'Run connected tools')

if __name__ == '__main__':
    unittest.main()
