import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from PIL import Image

from generated_media import image_asset_ids, allowed_download_url, download_generated_images


FILE_ID = "file_" + "a"*32
CONVERSATION_ID = "6ad14865-79dc-83ed-9373-7d986f2fa4db"


def conversation(pointer, role="tool"):
    return {
        "id": CONVERSATION_ID,
        "messages": [{
            "role": role,
            "content_type": "multimodal_text",
            "parts": [{"content_type": "image_asset_pointer", "asset_pointer": pointer}],
        }],
    }


class GeneratedMediaTests(unittest.TestCase):
    def test_extracts_tool_images_both_pointer_formats(self):
        for scheme in ("file-service", "sediment"):
            ptr = scheme + "://" + FILE_ID
            self.assertEqual(image_asset_ids(conversation(ptr)), [ptr])

    def test_ignores_uploaded_user_files(self):
        self.assertEqual(image_asset_ids(conversation("sediment://" + FILE_ID, "user")), [])

    def test_signed_url_must_be_trusted(self):
        self.assertTrue(allowed_download_url("https://chatgpt.com/backend-api/estuary/content?foo=bar"))
        self.assertFalse(allowed_download_url("https://chatgpt.com.attacker.com/"))
        self.assertFalse(allowed_download_url("http://chatgpt.com/file.png"))
        self.assertFalse(allowed_download_url("file:///etc/passwd"))

    def test_download_and_cache_image(self):
        image=Image.new("RGB",(32,20),"purple")
        buf=io.BytesIO();image.save(buf,format="PNG")
        response=Mock()
        response.content=buf.getvalue()
        response.raise_for_status.return_value=None
        client=Mock()
        client.request.return_value.json.return_value={
            "download_url":"https://chatgpt.com/backend-api/estuary/content?token=private"
        }
        client.http.get.return_value=response
        client.headers={}
        with tempfile.TemporaryDirectory() as folder:
            file=conversation("sediment://"+FILE_ID)
            saved=download_generated_images(client,file,Path(folder))
            self.assertEqual(len(saved),1)
            self.assertTrue(Path(saved[0]).is_file())
            self.assertEqual(Path(saved[0]).stat().st_mode & 0o777,0o600)
            self.assertEqual(download_generated_images(client,file,Path(folder)), saved)
            self.assertEqual(client.http.get.call_count,1)
        request_url=client.request.call_args.args[1]
        self.assertIn("/attachment/"+FILE_ID+"/download",request_url)

    def test_file_service_endpoint(self):
        client=Mock()
        client.request.return_value.json.return_value={"download_url":"https://bad.example/img.png"}
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"invalid image download URL"):
                download_generated_images(client, conversation("file-service://"+FILE_ID),Path(folder))
        self.assertIn("/files/"+FILE_ID+"/download",client.request.call_args.args[1])


if __name__=="__main__":
    unittest.main()
