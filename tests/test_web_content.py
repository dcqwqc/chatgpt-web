"""Terminal parity tests for structured ChatGPT response rendering."""
import unittest
from web_content import render_web_elements


class RichContentTests(unittest.TestCase):
    def test_standalone_rich_badge_keeps_status_tone(self):
        source = '<row>\n  <badge color="success">Verified</badge>\n</row>'
        rendered = render_web_elements(source, rich_ui=True)
        self.assertIn('::chatgpt-badge{label="Verified" tone="success"}', rendered)
        self.assertNotIn('<badge', rendered)
        self.assertEqual(render_web_elements('<badge color="info">Working</badge>', rich_ui=False),
                         '**Working**')

    def test_badge_unknown_color_and_unsafe_label_use_safe_fallback(self):
        self.assertIn('tone="neutral"', render_web_elements(
            '<badge color="unknown">Pending</badge>', rich_ui=True))
        self.assertNotIn('::chatgpt-badge', render_web_elements(
            '<badge color="info">a "quoted" value</badge>', rich_ui=True))

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
                         '::chatgpt-progress{value="73" tone="success"}')
        self.assertIn('━━━━', render_web_elements(source))

    def test_progress_tone_is_taken_from_source_fill(self):
        source = ('<box background="surface-tertiary" height="10px" radius="full" clip>'
                  '<box width="25%" height="100%" background="secondary"/>'
                  '</box>')
        self.assertEqual(render_web_elements(source, rich_ui=True).strip(),
                         '::chatgpt-progress{value="25" tone="neutral"}')

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


class LiteralCodeRegressionTests(unittest.TestCase):
    def test_interrupted_fence_never_promotes_code_to_ui(self):
        for fence in ('```html', '~~~~html', '````html'):
            source = fence + '\n<badge color="success">Verified</badge>\n<Chart title="Literal"/>'
            self.assertEqual(render_web_elements(source, rich_ui=True), source)

    def test_shorter_fence_cannot_close_longer_literal_example(self):
        source = '````markdown\n```html\n<badge>literal</badge>\n```\n````\n'
        self.assertEqual(render_web_elements(source, rich_ui=True), source)

    def test_codeblock_containing_fences_uses_longer_delimiter(self):
        output = render_web_elements('<CodeBlock language="markdown">```html\n<badge>literal</badge>\n```</CodeBlock>', rich_ui=True)
        self.assertTrue(output.startswith('\n````markdown\n'))
        self.assertTrue(output.endswith('\n````\n'))
        self.assertIn('<badge>literal</badge>', output)

    def test_interrupted_codeblock_keeps_literal_code(self):
        output = render_web_elements('<CodeBlock language="html"><badge>literal</badge>', rich_ui=True)
        self.assertEqual(output, '\n```html\n<badge>literal</badge>')

    def test_code_language_cannot_inject_a_new_fence(self):
        output = render_web_elements('<CodeBlock language="html\n::chatgpt-badge{}">text</CodeBlock>', rich_ui=True)
        self.assertEqual(output, '\n```\ntext\n```\n')

    def test_metric_row_retains_associated_value(self):
        source = '<row align="center" justify="between">\n<text color="secondary">Before — browser readiness</text>\n**4.06 s**\n</row>'
        self.assertIn('::chatgpt-metric{label="Before — browser readiness" value="4.06 s"}', render_web_elements(source, rich_ui=True))
        self.assertNotIn('::chatgpt-metric', render_web_elements(source))

    def test_metric_with_unsafe_attribute_uses_plain_fallback(self):
        source = '<row align="center" justify="between"><text>A "quote"</text>**3 s**</row>'
        self.assertNotIn('::chatgpt-metric', render_web_elements(source, rich_ui=True))

    def test_badge_backslash_cannot_escape_directive_delimiter(self):
        self.assertNotIn('::chatgpt-badge', render_web_elements('<badge>bad\\</badge>', rich_ui=True))

    def test_chart_fallback_retains_available_metadata_without_executing(self):
        output = render_web_elements('<Chart type="bar" title="Readiness" data={run()}/>', rich_ui=True)
        self.assertIn('Readiness (bar)', output)
        self.assertIn('data unavailable', output)
        self.assertNotIn('run()', output)


class InlineLiteralRegressionTests(unittest.TestCase):
    def test_inline_codeblock_tag_is_literal(self):
        for source in ('Use `<CodeBlock>literal</CodeBlock>` here.',
                       'Use ``<badge>literal ` tick</badge>`` here.'):
            self.assertEqual(render_web_elements(source, rich_ui=True), source)

if __name__ == '__main__':
    unittest.main()
