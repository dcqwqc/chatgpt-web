"""Grounded GenUI image metadata hydration without browser/model requests."""
import json
from pathlib import Path
import tempfile
import unittest
from genui_image_resolution import trusted_image_url, from_metadata, apply_resolved_images, hydrate_from_cache
from web_content import render_web_elements

INLINE='https://images.openai.com/static-rsc-4/'+'A'*56+'?purpose=inline'
FULL='https://images.openai.com/static-rsc-4/'+'B'*56+'?purpose=fullsize'
INLINE2='https://images.openai.com/static-rsc-4/'+'C'*56+'?purpose=inline'
TEXT='<AsyncImage query="Sample one" aspectRatio="4:3"/><AsyncImage query="Sample two"/>'
META={'genui_components':[
  {'type':'async_image','tree_range':[28,29], 'safe_urls':[{'url':'https://example.com/path'}, {'url':FULL},{'url':INLINE}]},
  {'type':'async_image','tree_range':[33,34], 'safe_urls':[{'url':INLINE2}]},
]}

class GenUIImageResolutionTests(unittest.TestCase):
 def test_exact_component_images_are_hydrated_and_viewable(self):
  images=from_metadata(META,TEXT)
  self.assertEqual(images,[{'fullsize':FULL,'inline':INLINE},{'inline':INLINE2}])
  transformed=apply_resolved_images(TEXT,images)
  rendered=render_web_elements(transformed)
  self.assertIn('![Image]('+INLINE+')',rendered)
  self.assertIn('[Image source]('+FULL+')',rendered)
  self.assertIn('![Image]('+INLINE2+')',rendered)
  self.assertLess(rendered.index(INLINE),rendered.index(INLINE2))

 def test_component_tree_order_overrides_metadata_array_order(self):
  reversed_meta={'genui_components': list(reversed(META['genui_components']))}
  self.assertEqual(from_metadata(reversed_meta,TEXT),from_metadata(META,TEXT))
  overlapping={'genui_components':[dict(META['genui_components'][0]),dict(META['genui_components'][1])]}
  overlapping['genui_components'][1]['tree_range']=[28,30]
  self.assertEqual(from_metadata(overlapping,TEXT),[])

 def test_count_mismatch_fails_closed(self):
  self.assertEqual(from_metadata(META,'<AsyncImage query="Only one"/>'),[])
  self.assertEqual(apply_resolved_images(TEXT,[{'inline':INLINE}]),TEXT)
  self.assertEqual(from_metadata({'genui_components':META['genui_components'][:1]},TEXT),[])

 def test_query_only_never_guesses_missing_metadata(self):
  self.assertEqual(apply_resolved_images(TEXT,None),TEXT)
  self.assertIn('Image search unavailable',render_web_elements(TEXT))

 def test_rejects_unsafe_urls_and_misleading_purposes(self):
  for url in ('javascript:alert(1)','http://images.openai.com/static-rsc-4/'+'X'*30+'?purpose=inline',
              'https://evil.example/static-rsc-4/'+'X'*30+'?purpose=inline',
              'https://images.openai.com@evil.example/static-rsc-4/'+'X'*30+'?purpose=inline',
              'https://images.openai.com:abc/static-rsc-4/'+'X'*30+'?purpose=inline',
              'https://images.openai.com/private/'+'X'*30+'?purpose=inline',
              FULL,'https://images.openai.com/static-rsc-4/'+'X'*30+'?purpose=inline&token=secret'):
   with self.subTest(url=url):
    self.assertIsNone(trusted_image_url(url,'inline'))

 def test_no_arbitrary_cache_access_and_exact_message_selection(self):
  cid='6af03ffb-8920-83eb-a2cc-8ceaf959bf9c'
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   data={'messages':[{'role':'assistant','text':TEXT,'resolved_images':from_metadata(META,TEXT)}]}
   (root/(cid+'.json')).write_text(json.dumps(data))
   resolved=hydrate_from_cache(TEXT,cid,root)
   self.assertIn(INLINE,resolved)
   self.assertEqual(hydrate_from_cache(TEXT,'../../etc/passwd',root),TEXT)
   data['messages'].append(data['messages'][0])
   (root/(cid+'.json')).write_text(json.dumps(data))
   self.assertEqual(hydrate_from_cache(TEXT,cid,root),TEXT)

if __name__=='__main__':unittest.main()
