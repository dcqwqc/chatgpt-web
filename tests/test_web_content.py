"""Terminal parity tests for structured ChatGPT response rendering."""
import unittest
from web_content import render_web_elements


class RichContentTests(unittest.TestCase):
    def test_badge_does_not_show_raw_markup(self):
        self.assertEqual(render_web_elements('<badge color="warning">High</badge>'),
                         '**High**')

    def test_web_link_remains_copyable(self):
        self.assertEqual(render_web_elements('<Link url="https://example.com">Details</Link>'),
                         '[Details](https://example.com)')

    def test_external_component_fallback_is_honest(self):
        self.assertIn('open in ChatGPT Web',
                      render_web_elements('<AsyncImage query="cat"/>'))
        self.assertIn('ChatGPT Web',
                      render_web_elements('<button>Execute</button>'))

    def test_fenced_source_is_not_modified(self):
        text = 'An example:\n' + chr(96)*3 + 'tsx\n<badge color="warning">High</badge>\n' + chr(96)*3 + '\n'
        self.assertEqual(render_web_elements(text), text)

    def test_raw_web_code_block_is_not_modified(self):
        source = '<CodeBlock language="html"><badge>literal tag</badge></CodeBlock>'
        output = render_web_elements(source)
        self.assertIn('<badge>literal tag</badge>', output)
        self.assertIn('html', output)
        self.assertNotIn('**literal tag**', output)

    def test_vector_and_app_have_web_fallback(self):
        self.assertIn('ChatGPT Web', render_web_elements('<svg><path d="X"/></svg>'))
        self.assertIn('ChatGPT Web', render_web_elements('<AppBlock><script>go()</script></AppBlock>'))

    def test_chatgpt_layout_bars_keep_fill_and_do_not_turn_into_code(self):
        source = ("## Results\n\n<box gap={2}>\n"
                  '  <row align="center" justify="between">\n'
                  '    <text color="secondary">Before</text>\n'
                  '    **4.06 s**\n'
                  '  </row>\n'
                  '  <box background="surface-tertiary" height="10px" radius="full" clip>\n'
                  '    <box background="secondary" width="100%" height="100%" radius="full"/>\n'
                  '  </box>\n'
                  '  <row>\n'
                  '    <text color="secondary">After</text>\n'
                  '    **2.82–2.97 s**\n'
                  '  </row>\n'
                  '  <box background="surface-tertiary" height="10px" radius="full" clip>\n'
                  '    <box background="success-solid" width="70%" height="100%" radius="full"/>\n'
                  '  </box>\n'
                  '</box>\n')
        rendered = render_web_elements(source)
        self.assertIn('Before\n**4.06 s**', rendered)
        self.assertIn('After\n**2.82–2.97 s**', rendered)
        self.assertIn('━━━━━━━━━━━━━━━━', rendered)
        self.assertIn('━━━━━━━━━━━─────', rendered)
        self.assertNotIn('    **4.06 s**', rendered)
        self.assertNotIn('<box', rendered)

    def test_native_progress_directive_only_when_explicitly_enabled(self):
        source = ('<box background="surface-tertiary" height="8px" radius="full" clip>'
                  '<box width="73%" height="100%" background="success-solid"/>'
                  '</box>')
        self.assertEqual(render_web_elements(source, rich_ui=True).strip(),
                         '::chatgpt-progress{value="73"}')
        self.assertIn('━━━━', render_web_elements(source))

    def test_directive_not_inserted_inside_fenced_source(self):
        example = ('```jsx\n<box background="surface-tertiary" height="8px" radius="full" clip>'
                   '<box width="73%" background="success-solid"/></box>\n```')
        self.assertEqual(render_web_elements(example, rich_ui=True), example)

    def test_layout_normalization_does_not_change_indented_code_outside_layout(self):
        source = '<row>\n  A paragraph\n</row>\n\n    a = 1\n    b = 2'
        rendered = render_web_elements(source)
        self.assertIn('A paragraph', rendered)
        self.assertIn('    a = 1\n    b = 2', rendered)

    def test_inline_code_is_not_modified(self):
        self.assertEqual(render_web_elements('Use ' + chr(96) + '<badge>High</badge>' +
                          chr(96) + ' in code.'), 'Use ' + chr(96) +
                         '<badge>High</badge>' + chr(96) + ' in code.')


if __name__ == '__main__':
    unittest.main()
