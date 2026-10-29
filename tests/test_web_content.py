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

    def test_inline_code_is_not_modified(self):
        self.assertEqual(render_web_elements('Use ' + chr(96) + '<badge>High</badge>' +
                          chr(96) + ' in code.'), 'Use ' + chr(96) +
                         '<badge>High</badge>' + chr(96) + ' in code.')


if __name__ == '__main__':
    unittest.main()
