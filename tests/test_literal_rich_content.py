import json
from pathlib import Path
import unittest
from rich_content import normalize_chart, literal_content
from web_content import render_web_elements, embedded_image_assets

CASES = json.loads((Path(__file__).resolve().parents[1] / 'bb-plugin-rich-ui/qa/rich-cases.json').read_text())


class LiteralRichTests(unittest.TestCase):
    def test_chart_contract_shared_with_frontend(self):
        for case in CASES:
            value = {k:v for k,v in case['value'].items() if k != 'kind'}
            with self.subTest(name=case['name']):
                if case['valid']:
                    self.assertEqual(normalize_chart(value), case['value'])
                else:
                    with self.assertRaises(ValueError):
                        normalize_chart(value)

    def test_actual_chart_content_becomes_closed_fence(self):
        spec = {k:v for k,v in CASES[0]['value'].items() if k != 'kind'}
        text = '<Chart content={' + json.dumps(spec) + '}/>'
        rendered = render_web_elements(text, rich_ui=True)
        self.assertIn('```chatgpt-rich\n', rendered)
        payload = json.loads(rendered.split('\n')[3])
        self.assertEqual(payload, CASES[0]['value'])
        self.assertNotIn('chatgpt-rich', render_web_elements(text))

    def test_chartjs_literal_schema_is_explicitly_normalized(self):
        spec = {'type':'line','data':{'labels':['A','B'],'datasets':[{'label':'Readings','data':[1.2,3]}]}}
        normalized = normalize_chart(spec)
        self.assertEqual(normalized['series'], [{'name':'Readings','values':[1.2,3]}])

    def test_quoted_json_attribute_and_bracket_boundary(self):
        raw = json.dumps({'type':'bar','labels':['A > B'],'series':[{'name':'N','values':[3]}]})
        attribute = 'content="' + raw.replace('"','&quot;') + '"'
        self.assertEqual(literal_content(attribute)['labels'], ['A > B'])
        self.assertIn('chatgpt-rich', render_web_elements('<Chart '+attribute+'/>', rich_ui=True))

    def test_rejects_expressions_duplicates_nonfinite_and_oversized(self):
        for attrs in ('content={run()}', 'content={data}', 'content={{"type":"bar","type":"line"}}',
                      'content={{"x":NaN}}', 'content={{"x":Infinity}}', 'content="'+ 'a'*33000 +'"'):
            with self.subTest(attrs=attrs[:80]), self.assertRaises((ValueError,RecursionError)):
                literal_content(attrs)
        for raw in ('<Chart content={run()}/>', '<Chart content={data}/>'):
            self.assertNotIn('chatgpt-rich',render_web_elements(raw, rich_ui=True))

    def test_fenced_examples_are_never_promoted(self):
        raw = '<Chart content={'+json.dumps({k:v for k,v in CASES[0]['value'].items() if k!='kind'})+'}/>'
        source = '```jsx\n'+raw+'\n```'
        self.assertEqual(render_web_elements(source,rich_ui=True),source)

    def test_literal_labels_are_not_transformed_as_markup(self):
        spec = {'type':'bar','labels':['<badge>Actual</badge>'],'series':[{'name':'N','values':[2]}]}
        rendered = render_web_elements('<Chart content={'+json.dumps(spec)+'}/>',rich_ui=True)
        self.assertIn('<badge>Actual</badge>',rendered)
        self.assertNotIn('**Actual**',rendered)

    def test_gallery_keeps_caption_order_and_unknown_items_honest(self):
        source = '<AsyncImageGroup caption="Evidence"><AsyncImage src="https://example.com/a.png" alt="First" caption="Real source"/><AsyncImage query="Missing"/></AsyncImageGroup>'
        output = render_web_elements(source,rich_ui=True)
        self.assertIn('"kind":"gallery"',output)
        self.assertIn('"caption":"Evidence"',output)
        self.assertIn('"caption":"Real source"',output)
        self.assertIn('"unavailable":',output)
        self.assertNotIn('"src":"Missing"',output)

    def test_controls_are_explicitly_static_and_do_not_resolve_state(self):
        self.assertIn('"kind":"control"',render_web_elements('<button onclick="run()">Save</button>',rich_ui=True))
        self.assertIn('"checked":true',render_web_elements('<checkbox checked label="Ready"/>',rich_ui=True))
        self.assertIn('"checked":false',render_web_elements('<checkbox checked={false} label="Ready"/>',rich_ui=True))
        self.assertNotIn('chatgpt-rich',render_web_elements('<checkbox checked={state} label="Ready"/>',rich_ui=True))

class NativeChartTests(unittest.TestCase):
    def test_native_schema_and_double_outer_braces(self):
        cases=json.loads((Path(__file__).resolve().parents[1] / 'bb-plugin-rich-ui/qa/native-chart-cases.json').read_text())
        for case in cases:
            with self.subTest(name=case['name']):
                self.assertEqual(normalize_chart(case['spec']),case['expected'])
                source='<Chart content={'+json.dumps(case['spec'])+'} />'
                rendered=render_web_elements(source,rich_ui=True)
                self.assertIn('```chatgpt-rich',rendered)
                self.assertEqual(json.loads(rendered.split('\n')[3]),case['expected'])

    def test_native_missing_values_and_ambiguous_series_are_rejected(self):
        base={'chartType':'bar','xKey':'x','series':[{'dataKey':'y','label':'Y'}],'data':[{'x':'A','y':1}]}
        import copy
        cases=[]
        v=copy.deepcopy(base);v['data'][0].pop('y');cases.append(v)
        v=copy.deepcopy(base);v['series'].append({'dataKey':'y','label':'Again'});cases.append(v)
        v=copy.deepcopy(base);v['data'][0]['y']=True;cases.append(v)
        v=copy.deepcopy(base);v['bounds']={'max':0};cases.append(v)
        v=copy.deepcopy(base);v['bounds']={'max':5};v['yMax']=5;cases.append(v)
        cases.append({'chartType':'pie','nameKey':'name','valueKey':'a','series':[{'dataKey':'b'}],'data':[{'name':'A','a':1,'b':2}]})
        for spec in cases:
            with self.subTest(spec=spec), self.assertRaises(ValueError):normalize_chart(spec)


class GalleryDedupeTests(unittest.TestCase):
    def test_rich_gallery_does_not_duplicate_hydrated_asset(self):
        asset='file_BBParityMetric20261108A'
        source='<AsyncImageGroup><AsyncImage ref="'+asset+'"/></AsyncImageGroup>'
        rendered=render_web_elements(source,rich_ui=True,image_assets=frozenset([asset]))
        self.assertEqual(embedded_image_assets(rendered),frozenset([asset]))
        invalid='```chatgpt-rich\n{"kind":"gallery","images":[{"src":"/api/v1/plugins/image-generation/http/cli-image?asset='+asset+'"}]}\n```'
        self.assertEqual(embedded_image_assets(invalid),frozenset())


class LiteralTokenBoundaryTests(unittest.TestCase):
    def test_backticks_in_chart_strings_are_data_not_inline_markdown(self):
        spec={'chartType':'bar','xKey':'x','series':[{'dataKey':'y'}],'data':[{'x':'`literal`','y':2}]}
        output=render_web_elements('<Chart content={'+json.dumps(spec)+'}/>',rich_ui=True)
        self.assertIn('chatgpt-rich',output)
        self.assertIn('`literal`',output)

    def test_gallery_caption_cannot_invent_child_images(self):
        output=render_web_elements('<AsyncImageGroup caption="&lt;AsyncImage src=&quot;https://example.com/fake.png&quot;/&gt;"><AsyncImage src="https://example.com/actual.png"/></AsyncImageGroup>',rich_ui=True)
        payload=json.loads(output.split('\n')[3])
        self.assertEqual(len(payload['images']),1)
        self.assertEqual(payload['images'][0]['src'],'https://example.com/actual.png')


class ExtremeLiteralTests(unittest.TestCase):
    def test_large_integer_falls_back_without_overflow(self):
        spec={'chartType':'bar','xKey':'x','series':[{'dataKey':'y'}],'data':[{'x':'A','y':10**1000}]}
        with self.assertRaises(ValueError):normalize_chart(spec)
        self.assertNotIn('chatgpt-rich',render_web_elements('<Chart content={'+json.dumps(spec)+'}/>',rich_ui=True))
