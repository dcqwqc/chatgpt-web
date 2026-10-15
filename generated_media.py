"""Save generated ChatGPT Web images as local, private image files.

Only tool/assistant image parts are considered: user-uploaded image assets
are not reclassified as generated files. Signed URLs are never logged.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import os
import re
import tempfile
from typing import Callable

from PIL import Image

MAX_SIZE = 45_000_000
ASSET_ID = re.compile(r"^file_[A-Za-z0-9]+$")
CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


def image_asset_ids(conversation: dict) -> list[str]:
    results: list[str] = []
    for message in conversation.get("messages", []):
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = (author.get("role") if isinstance(author, dict) else None) or message.get("role")
        if role not in ("tool", "assistant"):
            continue
        content = message.get("content")
        if not isinstance(content, dict):
            content = message
        for part in content.get("parts", []) or []:
            if not isinstance(part, dict) or part.get("content_type") != "image_asset_pointer":
                continue
            pointer = part.get("asset_pointer")
            if not isinstance(pointer, str):
                continue
            prefix = next((p for p in ("file-service://", "sediment://") if pointer.startswith(p)), None)
            if prefix is None:
                continue
            file_id = pointer[len(prefix):]
            if ASSET_ID.fullmatch(file_id) and pointer not in results:
                results.append(pointer)
    return results[:12]


def allowed_download_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    hostname = (parsed.hostname or "").lower()
    return (hostname == "chatgpt.com"
            or hostname.endswith(".oaiusercontent.com")
            or hostname.endswith(".openai.com"))


def detect_image_type(data: bytes) -> str:
    if data.startswith(bytes([137,80,78,71,13,10,26,10])):
        return ".png"
    if data.startswith(bytes([255,216,255])):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    raise ValueError("Downloaded content isn't a supported image")


def download_generated_images(client: object, conversation: dict, destination: Path) -> list[str]:
    conversation_id = conversation.get("conversation_id") or conversation.get("id")
    if not isinstance(conversation_id, str) or not CONVERSATION_ID.fullmatch(conversation_id):
        return []
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    outputs: list[str] = []
    for asset_pointer in image_asset_ids(conversation):
        asset_id = asset_pointer.split("://", 1)[1]
        # No secret signed URLs or generated content are sent to logs.
        existing = next(destination.glob(f"{asset_id}.*"), None)
        if existing is not None and existing.is_file() and existing.stat().st_size:
            outputs.append(str(existing))
            continue
        if asset_pointer.startswith("sediment://"):
            endpoint = f"/backend-api/conversation/{conversation_id}/attachment/{asset_id}/download"
        else:
            endpoint = f"/backend-api/files/{asset_id}/download"
        metadata = client.request("GET", "https://chatgpt.com" + endpoint).json()
        url = metadata.get("download_url") if isinstance(metadata, dict) else None
        if not isinstance(url, str) or not allowed_download_url(url):
            raise ValueError("ChatGPT returned an invalid image download URL")
        # Use the verified signed URL; do not follow redirects to arbitrary hosts.
        response = client.http.get(url, headers=client.headers if urlsplit(url).hostname == "chatgpt.com" else {}, follow_redirects=False, timeout=60)
        response.raise_for_status()
        data = response.content
        if not data or len(data) > MAX_SIZE:
            raise ValueError("Generated image is empty or exceeds size limit")
        extension = detect_image_type(data)
        output = destination / (asset_id + extension)
        fd, name = tempfile.mkstemp(prefix=".media-", dir=destination)
        try:
            with os.fdopen(fd,"wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            with Image.open(name) as im:
                im.verify()
            os.chmod(name, 0o600)
            os.replace(name, output)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        outputs.append(str(output))
    return outputs
