"""Readable, non-executing terminal fallbacks for ChatGPT rich UI markup.

Never evaluate bindings or actions; retain original text in stored transcripts.
"""
from __future__ import annotations
import re
from html import unescape
from urllib.parse import urlsplit
from rich_content import component_spans, rich_fence, replace_literal_charts, literal_json


def code_fence(body: str, language: str = '', *, closed: bool = True) -> str:
    """Fence literal code safely, including code containing Markdown fences."""
    language = language if re.fullmatch(r'[a-zA-Z0-9+#._-]{0,40}', language) else ''
    size = max([3] + [len(m.group()) + 1 for m in re.finditer(r'`{3,}', body)])
    fence = '`' * size
    return '\n' + fence + language + '\n' + body + ('\n' + fence + '\n' if closed else '')

INLINE = re.compile(r'(`[^`\n]+`)')
PROTECTED = re.compile(
    r'(?P<fence>^[ \t]{0,3}(?P<ticks>`{3,}|~{3,})[^\n]*\n)'
    r'|<CodeBlock\b[^>]*>'
    r'|(?P<rich_tag><(?:Chart|AsyncImageGroup|button|checkbox)\b)'
    r'|(?P<inline>`+)(?!`)[^\n]*?(?<!`)(?P=inline)(?!`)', re.M)


def protected_segments(text: str):
    """Keep complete AND interrupted code literal; never interpret code as UI."""
    end = 0
    while match := PROTECTED.search(text, end):
        yield False, text[end:match.start()]
        start = match.start()
        if match.group('inline'):
            end = match.end()
            yield True, text[start:end]
        elif match.group('rich_tag'):
            name=match.group('rich_tag')[1:]
            span=next(component_spans(text[start:],name),None)
            end=start+span[1] if span and span[0]==0 else match.end()
            yield False,text[start:end]
        elif match.group('fence'):
            ticks = match.group('ticks')
            close = re.search(r'^[ \t]{0,3}' + re.escape(ticks[0]) +
                              '{' + str(len(ticks)) + r',}[ \t]*(?:\n|$)',
                              text[match.end():], re.M)
            end = match.end() + close.end() if close else len(text)
            yield True, text[start:end]
        else:
            close = text.find('</CodeBlock>', match.end())
            end = close + len('</CodeBlock>') if close >= 0 else len(text)
            language = re.search(r'\blanguage="([^"]*)"', match.group())
            body = text[match.end():close if close >= 0 else len(text)]
            yield True, code_fence(body, language.group(1) if language else '', closed=close >= 0)
    yield False, text[end:]

PLAIN = 'text caption label row col box card grid grid-item flow flow-item list table table-section table-cell blockquote form carousel carousel-item'.split()
NEWLINES = 'row col box card grid-item table-row list-item'.split()


# These are rendered ChatGPT layout tracks, not executable HTML. The native
# ChatGPT client draws them as rounded horizontal bars; BB understands Markdown
# rather than DIL, so preserve their relative fill instead of deleting them.
PROGRESS_TRACK = re.compile(
    r'<box\b(?=[^>]*background="surface-tertiary")(?=[^>]*height="(?:8|10)px")[^>]*>'
    r'\s*<box\b(?=[^>]*background="[^"]+")(?=[^>]*width="(\d{1,3})%")'
    r'[^>]*/>\s*</box>', re.S)
LAYOUT_TAG = re.compile(r'</?(?:box|row|col|grid|grid-item|flow|flow-item|card)\b[^>]*>')
BADGE_LINE = re.compile(r'(?m)^[ \t]*<badge\b([^>]*)>([^<>\n]{1,100})</badge>[ \t]*$')


def preserve_progress(fragment: str, *, rich_ui: bool = False) -> str:
    def substitute(match: re.Match[str]) -> str:
        percent = min(100, max(0, int(match.group(1))))
        if rich_ui:
            fills = re.findall(r'background="([^"\n]+)"', match.group(0))
            fill = fills[-1] if fills else ""
            tone = ("success" if fill in ("success-solid", "success") else
                    "warning" if fill in ("warning-solid", "warning") else
                    "danger" if fill in ("danger-solid", "destructive", "error") else
                    "neutral")
            return f'\n\n::chatgpt-progress{{value="{percent}" tone="{tone}"}}\n\n'
        filled = round(percent * 16 / 100)
        return '\n' + '━' * filled + '─' * (16 - filled) + '\n'
    return PROGRESS_TRACK.sub(substitute, fragment)


def preserve_status_badges(fragment: str) -> str:
    """Promote safe standalone status badges into BB-native directives."""
    def convert(match: re.Match[str]) -> str:
        label = match.group(2).strip()
        if not label or any(mark in label for mark in ('"', '{', '}', '\\')):
            return '**' + label + '**'
        color = re.search(r'\bcolor="([^"\n]+)"', match.group(1))
        raw = color.group(1) if color else "neutral"
        tone = (raw if raw in ('success', 'info', 'warning', 'danger') else
                'danger' if raw in ('error', 'destructive') else 'neutral')
        return f'\n::chatgpt-badge{{label="{label}" tone="{tone}"}}\n'
    return BADGE_LINE.sub(convert, fragment)


METRIC_ROW = re.compile(
    r'<row\b(?=[^>]*\balign="center")(?=[^>]*\bjustify="between")[^>]*>'
    r'\s*<text\b[^>]*>([^<>\n]{1,160})</text>\s*\*\*([^*<>\n]{1,80})\*\*\s*</row>', re.S)


def preserve_metric_rows(fragment: str) -> str:
    def convert(match: re.Match[str]) -> str:
        label, value = (part.strip() for part in match.groups())
        if any(c in label + value for c in ('"', '{', '}', '\\')):
            return match.group()
        return f'\n\n::chatgpt-metric{{label="{label}" value="{value}"}}\n\n'
    return METRIC_ROW.sub(convert, fragment)


def chart_fallback(match: re.Match[str]) -> str:
    # Preserve available metadata, without evaluating JSX expressions or data.
    opening = match.group().split('>', 1)[0]
    title = re.search(r'\btitle="([^"<>\n]{1,160})"', opening)
    kind = re.search(r'\btype="([a-zA-Z -]{1,30})"', opening)
    detail = ': ' + title.group(1) if title else ''
    detail += ' (' + kind.group(1) + ')' if kind else ''
    return '[Chart' + detail + ' — open in ChatGPT Web; chart data unavailable here]'


# The native ChatGPT client can resolve AsyncImage.query/ref server-side.
# A plain ACP transcript only includes that markup, not its resolved pixels.
# Preserve images with a real source URL. Never invent one from a query.
IMAGE_SOURCE = re.compile(r'<AsyncImage\b([^<>]*?)/>', re.S)
BB_IMAGE_ROUTE = re.compile(
    r'^/api/v1/plugins/image-generation/http/(?:'
    r'cli-image\?asset=file_[A-Za-z0-9]{12,80}|'
    r'worker-image\?asset=[a-f0-9]{32})$'
)


def image_attribute(attributes: str, key: str) -> str | None:
    match = re.search(r'(?<![-\w])' + re.escape(key) + r'="([^"\n]*)"', attributes)
    return unescape(match.group(1)) if match else None


def safe_image_url(source: str) -> str | None:
    """Use actual image URLs only, converting our own loopback host to BB-relative."""
    if BB_IMAGE_ROUTE.fullmatch(source):
        return source
    try:
        url = urlsplit(source)
        port = url.port
    except ValueError:
        return None
    if (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')
            and port == 38886 and not url.username and not url.password):
        path = url.path + ('?' + url.query if url.query else '')
        return path if BB_IMAGE_ROUTE.fullmatch(path) else None
    if (url.scheme != 'https' or not url.netloc or not url.hostname
            or url.username or url.password or any(c.isspace() for c in source)
            or any(c in source for c in ('<', '>', '`', chr(92)))):
        return None
    # Markdown URLs must not contain unescaped parentheses.
    def escape(value: str) -> str:
        return value.replace('(', '%28').replace(')', '%29').replace('[', '%5B').replace(']', '%5D')
    return url._replace(path=escape(url.path), query=escape(url.query),
                        fragment=escape(url.fragment)).geturl()


def image_with_source(match: re.Match[str], *, image_assets: frozenset[str] = frozenset()) -> str:
    attributes = match.group(1)
    alt = image_attribute(attributes, 'alt') or 'Image'
    alt = re.sub(r'[\[\]\r\n]', ' ', alt).strip()[:120] or 'Image'
    src = image_attribute(attributes, 'src')
    url = safe_image_url(src) if src else None
    reference = image_attribute(attributes, 'ref')
    if not src and reference:
        asset = re.fullmatch(r'(?:(?:file-service|sediment)://)?(file_[A-Za-z0-9]{12,80})', reference)
        if asset and asset.group(1) in image_assets:
            url = '/api/v1/plugins/image-generation/http/cli-image?asset=' + asset.group(1)
    if url:
        # Keep the image itself clickable by BB's native lightbox. Sources are
        # separate links rather than anchors that swallow the image click.
        citation = image_attribute(attributes, 'href')
        citation_url = safe_image_url(citation) if citation else None
        source_link = f'\n\n[Image source]({citation_url})' if citation_url else ''
        return f'\n\n![{alt}]({url}){source_link}\n\n'
    query = image_attribute(attributes, 'query')
    if query:
        description = re.sub(r'[\r\n\[\]]', ' ', query).strip()[:120]
        return f'[Image search unavailable in BB: {description} — view in ChatGPT Web]'
    if reference:
        label = re.sub(r'[\r\n\[\]`\\]', ' ', reference).strip()[:100]
        return f'[Image reference unavailable in BB: {label} — view in ChatGPT Web]'
    return f'[Image source unavailable in BB: {alt}]'


def rich_gallery(fragment: str, image_assets: frozenset[str]) -> str:
    result, end = [], 0
    for start, stop, attributes in component_spans(fragment, 'AsyncImageGroup'):
        source = fragment[start:stop]
        body=source[len('<AsyncImageGroup')+len(attributes)+1:-(len('</AsyncImageGroup>'))]
        matches = list(IMAGE_SOURCE.finditer(body))
        result.append(fragment[end:start])
        items = []
        for match in matches[:12]:
            attrs = match.group(1)
            rendered = image_with_source(match, image_assets=image_assets)
            embedded = re.search(r'!\[([^\n]*)\]\(([^\n]+)\)', rendered)
            if not embedded:
                items.append({'unavailable': rendered})
                continue
            item = {'src': embedded.group(2), 'alt': embedded.group(1)}
            for key in ('caption', 'href'):
                value = image_attribute(attrs, key)
                if value and ((key == 'caption' and len(value) <= 160 and not any(ord(c)<32 for c in value)) or
                              (key == 'href' and safe_image_url(value))):
                    item[key] = safe_image_url(value) if key == 'href' else value
            items.append(item)
        caption = image_attribute(attributes, 'caption') or image_attribute(attributes, 'title')
        caption = caption if caption and caption.strip() else None
        remainder = IMAGE_SOURCE.sub('', body).strip()
        if items and all(len(i.get('src',''))<=4000 for i in items) and len(matches) <= 12 and not remainder and (not caption or len(caption) <= 160 and not any(ord(c)<32 for c in caption)):
            result.append(rich_fence({'kind': 'gallery', 'images': items, **({'caption': caption} if caption else {})}))
        else:
            result.append(source)
        end = stop
    return ''.join(result) + fragment[end:]


def rich_static_controls(fragment: str) -> str:
    def button(match):
        label = unescape(match.group(1)).strip()
        if not label or len(label) > 160 or any(ord(c)<32 for c in label):
            return match.group()
        return rich_fence({'kind':'control', 'type':'button', 'label':label})
    value = re.sub(r'<button\b[^>]*>([^<>]*)</button>', button, fragment)
    def checkbox(match):
        attrs = match.group(1)
        label = image_attribute(attrs, 'label')
        if not label or len(label)>160 or any(ord(c)<32 for c in label):
            return match.group()
        checked = re.search(r'(?<![-\w])checked(?:\s*=\s*(?:\{(true|false)\}|"(true|false)"))?(?=\s|/|$)', attrs)
        if re.search(r'(?<![-\w])checked\s*=',attrs) and (not checked or '=' not in checked.group()):
            return match.group()
        state = bool(checked) and (checked.group(1) or checked.group(2) or 'true') == 'true'
        return rich_fence({'kind':'control','type':'checkbox','label':label,'checked':state})
    return re.sub(r'<checkbox\b([^<>]*?)/>', checkbox, value)


def normalize_layout_indentation(fragment: str) -> str:
    """Prevent JSX indentation from becoming unintended Markdown code blocks."""
    if '<box' not in fragment and '<row' not in fragment:
        return fragment
    depth = 0
    lines = []
    for line in fragment.splitlines(keepends=True):
        before = depth
        tags = list(LAYOUT_TAG.finditer(line))
        for tag in tags:
            t = tag.group()
            if t.startswith('</'):
                depth = max(0, depth - 1)
            elif not t.endswith('/>'):
                depth += 1
        lines.append(line.lstrip(' \t') if before or depth or tags else line)
    return ''.join(lines)


def flatten(fragment: str, *, rich_ui: bool = False, image_assets: frozenset[str] = frozenset()) -> str:
    if '<' not in fragment:
        return fragment
    value = normalize_layout_indentation(preserve_progress(fragment, rich_ui=rich_ui))
    if rich_ui:
        value = preserve_metric_rows(preserve_status_badges(value))
    value = re.sub(r'<AppBlock\b[^>]*>.*?</AppBlock>', '[Interactive app — open in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<svg\b[^>]*>.*?</svg>', '[Vector image — open in ChatGPT Web]', value, flags=re.S)
    # Strip only the gallery container; retain ordered, individually validated
    # child images. A self-closing group has no explicit child assets to retain.
    value = re.sub(r'<AsyncImageGroup\b[^>]*?/>', '[Image gallery sources unavailable in BB — view in ChatGPT Web]', value)
    value = re.sub(r'</?AsyncImageGroup\b[^>]*>', '\n\n', value)
    value = IMAGE_SOURCE.sub(lambda match: image_with_source(match, image_assets=image_assets), value)
    value = re.sub(r'<Chart\b[^>]*(?:/>|>.*?</Chart>)', chart_fallback, value, flags=re.S)
    for tag, label in [('AsyncVideo','Video'),('MapWidgetV2','Map'),
                       ('FileNavList','Files')]:
        value = re.sub(r'<'+tag+r'\b[^>]*(?:/>|>.*?</'+tag+r'>)',
                       '['+label+' — open in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<(?:badge|bold|title)\b[^>]*>', '**', value)
    value = re.sub(r'</(?:badge|bold|title)>', '**', value)
    value = re.sub(r'<italic\b[^>]*>', '*', value).replace('</italic>', '*')
    value = re.sub(r'<strikethrough\b[^>]*>', '~~', value).replace('</strikethrough>', '~~')
    value = re.sub(r'<CodeBlock(?:\s+language="([^"]+)")?[^>]*>',
                   lambda m: '\n'+'`'*3+(m.group(1) or '')+'\n', value)
    value = value.replace('</CodeBlock>', '\n'+'`'*3+'\n')
    value = re.sub(r'<WritingBlock\b[^>]*>', '\n**Writing draft**\n\n', value)
    value = value.replace('</WritingBlock>', '\n')
    def link(m: re.Match[str]) -> str:
        attrs, label = m.group(1), m.group(2)
        url = re.search(r'\burl="(https?://[^"]+)"', attrs)
        return f'[{label}]({url.group(1)})' if url else label
    value = re.sub(r'<Link\b([^>]*)>(.*?)</Link>', link, value, flags=re.S)
    value = re.sub(r'<button\b[^>]*>(.*?)</button>',
                   r'[Button: \1 — interactive in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<pressable\b[^>]*>(.*?)</pressable>',
                   r'[Interactive: \1 — open in ChatGPT Web]', value, flags=re.S)
    for tag in ('input','select','checkbox','textarea','radio','slider','date-picker',
                'segmented-control','radio-group'):
        value = re.sub(r'<'+tag+r'\b[^>]*(?:/>|>.*?</'+tag+r'>)',
                       '['+tag+' — interactive in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<icon\b[^>]*\bname="([^"]+)"[^>]*/>',
                   lambda m: {'check':'✓','copy':'⧉','alert-triangle':'!','x':'×'}.get(m.group(1), ''), value)
    value = re.sub(r'<Entity\b[^>]*\bvalue="([^"]+)"[^>]*/>', r'\1', value)
    def citation_link(match: re.Match[str]) -> str:
        attrs = match.group(1)
        raw = image_attribute(attrs, 'url') or image_attribute(attrs, 'href')
        url = safe_image_url(raw) if raw else None
        label = image_attribute(attrs, 'title') or 'Source'
        label = re.sub(r'[\[\]\r\n`\\]', ' ', label).strip()[:120] or 'Source'
        return f'[{label}]({url})' if url else '[Citation target unavailable in BB]'
    value = re.sub(r'<Cite\b([^>]*)/>', citation_link, value)
    value = re.sub(r'<(?:FileCite|MemoryCite)\b[^>]*/>', '[Citation target unavailable in BB]', value)
    value = re.sub(r'<divider\b[^>]*/>', '\n---\n', value)
    value = re.sub(r'<table-row\b[^>]*>', '\n• ', value)
    value = re.sub(r'<table-cell\b[^>]*>', '  |  ', value)
    value = re.sub(r'<list-item\b[^>]*>', '\n- ', value)
    for tag in PLAIN + NEWLINES + ['grid','flow','table-row','list-item','favicon',
                                   'popover','popover-trigger','popover-content']:
        value = re.sub(r'</?'+tag+r'\b[^>]*>', '\n' if tag in NEWLINES else '', value)
    return re.sub(r'\n{4,}', '\n\n\n', value)


def render_web_elements(text: str, *, rich_ui: bool = False, image_assets: frozenset[str] = frozenset()) -> str:
    """Format rich ChatGPT text; preserve fenced and inline code verbatim."""
    if '<' not in text:
        return text
    result: list[str] = []
    for protected, chunk in protected_segments(text):
        if protected:
            result.append(chunk)
        else:
            if rich_ui:
                chunk = replace_literal_charts(chunk, lambda source: chart_fallback(re.match(r'.*', source, re.S)))
                # Generated fences are protected before any remaining HTML/layout
                # normalization, so labels and data stay exactly as serialized.
                pieces = []
                for locked, part in protected_segments(chunk):
                    pieces.append(part if locked else rich_gallery(part, image_assets))
                chunk = ''.join(pieces)
                pieces = []
                for locked, part in protected_segments(chunk):
                    pieces.append(part if locked else rich_static_controls(part))
                chunk = ''.join(pieces)
            for locked, part in protected_segments(chunk):
                if locked:
                    result.append(part)
                else:
                    result.extend(flatten(item, rich_ui=rich_ui, image_assets=image_assets) if n % 2 == 0 else item
                                  for n, item in enumerate(INLINE.split(part)))
    return ''.join(result)


def embedded_image_assets(text: str) -> frozenset[str]:
    """Find rendered image links or validated gallery fences, not code examples."""
    pattern = re.compile(r'!\[[^\n]*?\]\(/api/v1/plugins/image-generation/http/cli-image\?asset=(file_[A-Za-z0-9]{12,80})\)')
    assets = set()
    for protected, chunk in protected_segments(text):
        if not protected:
            assets.update(match.group(1) for match in pattern.finditer(chunk))
            continue
        fence = re.fullmatch(r'[ \t]{0,3}(`{3,})chatgpt-rich\n(.*?)\n\1[ \t]*\n?', chunk, re.S)
        if not fence:
            continue
        try:
            value=literal_json(fence.group(2))
        except (ValueError,RecursionError):
            continue
        def label(v,n):
            return isinstance(v,str) and 0<len(v.strip())<=n and not any(ord(c)<32 for c in v)
        if not isinstance(value,dict) or value.get('kind')!='gallery' or set(value)-{'kind','images','caption'} or 'caption' in value and not label(value['caption'],160):
            continue
        images=value.get('images')
        if not isinstance(images,list) or not 1<=len(images)<=12:
            continue
        valid=True
        for item in images:
            if not isinstance(item,dict):
                valid=False;break
            if set(item)=={'unavailable'} and label(item['unavailable'],240):
                continue
            if set(item)-{'src','alt','caption','href'} or not isinstance(item.get('src'),str) or len(item['src'])>4000 or not (BB_IMAGE_ROUTE.fullmatch(item['src']) or item['src'].startswith('https://') and safe_image_url(item['src'])) or not label(item.get('alt'),120) or 'caption' in item and not label(item['caption'],160) or 'href' in item and (not isinstance(item['href'],str) or not safe_image_url(item['href'])):
                valid=False;break
        if valid:
            for item in images:
                match=re.fullmatch(r'/api/v1/plugins/image-generation/http/cli-image\?asset=(file_[A-Za-z0-9]{12,80})',item.get('src',''))
                if match:
                    assets.add(match.group(1))
    return frozenset(assets)
