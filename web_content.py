"""Readable, non-executing terminal fallbacks for ChatGPT rich UI markup.

Never evaluate bindings or actions; retain original text in stored transcripts.
"""
from __future__ import annotations
import re


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


def flatten(fragment: str, *, rich_ui: bool = False) -> str:
    if '<' not in fragment:
        return fragment
    value = normalize_layout_indentation(preserve_progress(fragment, rich_ui=rich_ui))
    if rich_ui:
        value = preserve_metric_rows(preserve_status_badges(value))
    value = re.sub(r'<AppBlock\b[^>]*>.*?</AppBlock>', '[Interactive app — open in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<svg\b[^>]*>.*?</svg>', '[Vector image — open in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<Chart\b[^>]*(?:/>|>.*?</Chart>)', chart_fallback, value, flags=re.S)
    for tag, label in [('AsyncImage','Image'),('AsyncImageGroup','Image gallery'),
                       ('AsyncVideo','Video'),('MapWidgetV2','Map'),
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
    value = re.sub(r'<(?:Cite|FileCite|MemoryCite)\b[^>]*/>', '[citation]', value)
    value = re.sub(r'<divider\b[^>]*/>', '\n---\n', value)
    value = re.sub(r'<table-row\b[^>]*>', '\n• ', value)
    value = re.sub(r'<table-cell\b[^>]*>', '  |  ', value)
    value = re.sub(r'<list-item\b[^>]*>', '\n- ', value)
    for tag in PLAIN + NEWLINES + ['grid','flow','table-row','list-item','favicon',
                                   'popover','popover-trigger','popover-content']:
        value = re.sub(r'</?'+tag+r'\b[^>]*>', '\n' if tag in NEWLINES else '', value)
    return re.sub(r'\n{4,}', '\n\n\n', value)


def render_web_elements(text: str, *, rich_ui: bool = False) -> str:
    """Format rich ChatGPT text; preserve fenced and inline code verbatim."""
    if '<' not in text:
        return text
    result: list[str] = []
    for protected, chunk in protected_segments(text):
        if protected:
            result.append(chunk)
        else:
            result.extend(flatten(part, rich_ui=rich_ui) if n % 2 == 0 else part
                          for n, part in enumerate(INLINE.split(chunk)))
    return ''.join(result)
