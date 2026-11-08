import unittest
from unittest.mock import patch

import bb_acp
from web_content import render_web_elements, safe_image_url, embedded_image_assets

ASSET = 'file_BBParityMetric20261108A'
ROUTE = '/api/v1/plugins/image-generation/http/cli-image?asset=' + ASSET


class MediaRenderingTests(unittest.TestCase):
    def test_gallery_retains_all_explicit_children_in_order(self):
        source = ('<AsyncImageGroup><AsyncImage src="https://example.com/first.png" alt="First"/>'
                  '<AsyncImage src="https://example.com/second.png" alt="Second"/></AsyncImageGroup>')
        output = render_web_elements(source)
        self.assertIn('![First](https://example.com/first.png)', output)
        self.assertIn('![Second](https://example.com/second.png)', output)
        self.assertLess(output.index('First'), output.index('Second'))
        self.assertNotIn('gallery', output)

    def test_gallery_does_not_discard_known_image_because_a_sibling_is_unresolved(self):
        output = render_web_elements('<AsyncImageGroup><AsyncImage query="Missing"/>'
                                     '<AsyncImage src="https://example.com/known.png"/></AsyncImageGroup>')
        self.assertIn('Missing', output)
        self.assertIn('![Image](https://example.com/known.png)', output)

    def test_self_closing_group_never_evaluates_data(self):
        output = render_web_elements('<AsyncImageGroup images={run()}/>')
        self.assertIn('sources unavailable', output)
        self.assertNotIn('run()', output)
        self.assertNotIn('![', output)

    def test_exact_asset_reference_only_uses_verified_asset_set(self):
        for ref in (ASSET, 'file-service://' + ASSET, 'sediment://' + ASSET):
            source = f'<AsyncImage ref="{ref}" alt="Metric screenshot"/>'
            self.assertNotIn('![', render_web_elements(source))
            self.assertIn('![Metric screenshot](' + ROUTE + ')',
                          render_web_elements(source, image_assets=frozenset([ASSET])))

    def test_query_alias_and_paths_cannot_guess_cache_ids(self):
        for attr in ('query="file_BBParityMetric20261108A"', 'ref="turn0image0"',
                     'ref="/tmp/private.png"', 'ref="file-service://../../etc/passwd"'):
            output = render_web_elements('<AsyncImage ' + attr + '/>', image_assets=frozenset([ASSET]))
            self.assertNotIn('![', output)
            self.assertIn('unavailable', output)

    def test_image_citation_is_separate_from_lightbox_image(self):
        output = render_web_elements('<AsyncImage src="https://example.com/image.png" '
                                     'href="https://example.com/article" alt="Evidence"/>')
        self.assertIn('![Evidence](https://example.com/image.png)', output)
        self.assertIn('[Image source](https://example.com/article)', output)
        self.assertNotIn('[![', output)

    def test_explicit_citation_link_and_unknown_reference(self):
        self.assertEqual(render_web_elements('<Cite url="https://example.com/article" title="Evidence"/>'),
                         '[Evidence](https://example.com/article)')
        for source in ('<Cite ref="turn0search0"/>', '<FileCite path="/etc/passwd"/>',
                       '<Cite url="javascript:alert(1)"/>'):
            self.assertIn('target unavailable', render_web_elements(source))

    def test_worker_route_matches_actual_server_contract(self):
        route = '/api/v1/plugins/image-generation/http/worker-image?asset=' + 'a' * 32
        self.assertEqual(safe_image_url('http://127.0.0.1:38886' + route), route)
        self.assertIsNone(safe_image_url(route.replace('a' * 32, ASSET)))

    def test_malformed_ports_are_rejected_without_crashing(self):
        for url in ('http://127.0.0.1:bad/image.png', 'https://example.com:99999/image.png'):
            self.assertIsNone(safe_image_url(url))

    def test_code_examples_do_not_resolve_groups_or_asset_refs(self):
        source = '```jsx\n<AsyncImageGroup><AsyncImage ref="' + ASSET + '"/></AsyncImageGroup>\n```'
        self.assertEqual(render_web_elements(source, image_assets=frozenset([ASSET])), source)


class AdapterMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_resolves_only_current_conversation_verified_media(self):
        server = bb_acp.Server()
        server.sessions['session'] = {'conversation_id': 'current-conversation'}
        records = []
        async def emit(record):
            records.append(record)
        server.emit = emit
        with patch.object(bb_acp, 'generated_media', return_value=[ASSET]) as assets:
            await server.update('session', '<AsyncImage ref="' + ASSET + '"/>')
        assets.assert_called_once_with('current-conversation')
        self.assertIn(ROUTE, records[0]['params']['update']['content']['text'])

    async def test_missing_session_does_not_invent_assets(self):
        server = bb_acp.Server()
        records = []
        async def emit(record):
            records.append(record)
        server.emit = emit
        await server.update('missing', '<AsyncImage ref="' + ASSET + '"/>')
        self.assertNotIn('![', records[0]['params']['update']['content']['text'])

    async def test_plain_commentary_does_not_scan_media_cache(self):
        server = bb_acp.Server()
        async def emit(record):
            pass
        server.emit = emit
        with patch.object(bb_acp, 'generated_media') as assets:
            await server.update('missing', 'Plain commentary')
        assets.assert_not_called()


class EmbeddedAssetTests(unittest.TestCase):
    def test_only_rendered_images_count_for_deduplication(self):
        image = '![Screenshot](' + ROUTE + ')'
        self.assertEqual(embedded_image_assets(image), frozenset([ASSET]))
        self.assertEqual(embedded_image_assets('```markdown\n' + image + '\n```'), frozenset())
        self.assertEqual(embedded_image_assets('`' + image + '`'), frozenset())
        self.assertEqual(embedded_image_assets('[Source](' + ROUTE + ')'), frozenset())
