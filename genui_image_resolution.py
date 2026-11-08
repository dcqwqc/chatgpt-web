"""Use exact saved ChatGPT GenUI image metadata; never infer image search hits.

No external requests are performed. Returned URLs are from this same message's
component resolution, not a fresh search or guessed source.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

IMAGE_TAG = re.compile(r'<AsyncImage\b([^<>]*?)/>', re.S)
MAX_RESOLUTIONS = 32


def trusted_image_url(value: object, purpose: str) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or any(x in value for x in ('<', '>', '\\', '`', '\n', '\r')):
        return None
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return None
    if (url.scheme != 'https' or url.hostname != 'images.openai.com'
            or url.username or url.password or port is not None
            or not url.path.startswith('/static-rsc-')
            or not re.fullmatch(r'/static-rsc-[0-9]+/[A-Za-z0-9_-]{20,900}', url.path)
            or not url.query or parse_qs(url.query) != {'purpose': [purpose]}
            or url.fragment):
        return None
    return value


def from_metadata(metadata: object, text: str) -> list[dict[str, str]]:
    """Return mappings only when component count exactly matches source tags."""
    if not isinstance(metadata, dict) or not isinstance(text, str):
        return []
    components = metadata.get('genui_components')
    if not isinstance(components, list) or len(components) > MAX_RESOLUTIONS:
        return []
    images = [x for x in components if isinstance(x, dict) and x.get('type') == 'async_image']
    if not images or len(images) != len(IMAGE_TAG.findall(text)):
        return []
    ranges = [x.get('tree_range') for x in images]
    if all(isinstance(r, list) and len(r) == 2 and
           all(type(n) is int and n >= 0 for n in r) and r[0] < r[1]
           for r in ranges):
        images.sort(key=lambda item: item['tree_range'][0])
        if any(a['tree_range'][1] > b['tree_range'][0]
               for a, b in zip(images, images[1:])):
            return []
    output = []
    for image in images:
        source_urls = image.get('safe_urls')
        if not isinstance(source_urls, list) or len(source_urls) > 12:
            return []
        chosen = {}
        for entry in source_urls:
            if not isinstance(entry, dict):
                continue
            for purpose in ('inline', 'fullsize'):
                url = trusted_image_url(entry.get('url'), purpose)
                if url and purpose not in chosen:
                    chosen[purpose] = url
        if 'inline' not in chosen:
            return []
        output.append(chosen)
    return output


def apply_resolved_images(text: str, images: object) -> str:
    """Hydrate exact image tags in order; never substitute on a count mismatch."""
    if not isinstance(text, str) or not isinstance(images, list) or len(images) != len(IMAGE_TAG.findall(text)):
        return text
    index = 0
    def replace(match: re.Match[str]) -> str:
        nonlocal index
        record = images[index]
        index += 1
        if not isinstance(record, dict):
            return match.group(0)
        inline = trusted_image_url(record.get('inline'), 'inline')
        if not inline or re.search(r'\bsrc\s*=', match.group(1)):
            return match.group(0)
        fullsize = trusted_image_url(record.get('fullsize'), 'fullsize')
        # Keep literal source query for alt text in BB fallback handling.
        additions = ' src="' + inline + '"'
        if fullsize:
            additions += ' href="' + fullsize + '"'
        return '<AsyncImage' + match.group(1) + additions + '/>'
    return IMAGE_TAG.sub(replace, text)


def hydrate_from_cache(text: str, conversation_id: object, root: Path) -> str:
    """Use only the matching cached saved assistant message; no new requests."""
    if '<AsyncImage' not in text or not isinstance(conversation_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', conversation_id):
        return text
    try:
        record = json.loads((root / (conversation_id + '.json')).read_text(encoding='utf8'))
    except (OSError, ValueError, UnicodeError):
        return text
    messages = record.get('messages') if isinstance(record, dict) else None
    if not isinstance(messages, list):
        return text
    matches = [m for m in messages if isinstance(m, dict) and m.get('role') == 'assistant'
               and isinstance(m.get('text'), str) and m['text'].strip() == text.strip()
               and isinstance(m.get('resolved_images'), list)]
    if len(matches) != 1:
        return text
    return apply_resolved_images(text, matches[0]['resolved_images'])
