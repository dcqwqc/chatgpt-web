"""Readable, non-executing terminal fallbacks for ChatGPT rich UI markup.

Never evaluate bindings or actions; retain original text in stored transcripts.
"""
from __future__ import annotations
import re

FENCE = re.compile(r'(?ms)^\s*`{3}[^\n]*\n.*?^\s*`{3}(?:\n|$)')
INLINE = re.compile(r'(`[^`\n]+`)')
PROTECTED = re.compile(FENCE.pattern + r"|<CodeBlock\b[^>]*>.*?</CodeBlock>", re.M | re.S)
PLAIN = 'text caption label row col box card grid grid-item flow flow-item list table table-section table-cell blockquote form carousel carousel-item'.split()
NEWLINES = 'row col box card grid-item table-row list-item'.split()


def flatten(fragment: str) -> str:
    if '<' not in fragment:
        return fragment
    value = fragment
    value = re.sub(r'<AppBlock\b[^>]*>.*?</AppBlock>', '[Interactive app — open in ChatGPT Web]', value, flags=re.S)
    value = re.sub(r'<svg\b[^>]*>.*?</svg>', '[Vector image — open in ChatGPT Web]', value, flags=re.S)
    for tag, label in [('AsyncImage','Image'),('AsyncImageGroup','Image gallery'),
                       ('AsyncVideo','Video'),('Chart','Chart'),('MapWidgetV2','Map'),
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


def render_web_elements(text: str) -> str:
    """Format rich ChatGPT text; preserve fenced and inline code verbatim."""
    if '<' not in text:
        return text
    result: list[str] = []
    end = 0
    for fence in PROTECTED.finditer(text):
        before = text[end:fence.start()]
        result.extend(flatten(chunk) if n % 2 == 0 else chunk
                      for n, chunk in enumerate(INLINE.split(before)))
        raw = fence.group()
        if raw.startswith("<CodeBlock"):
            opening, _, remaining = raw.partition(">")
            language = re.search(r'language="([^"]+)"', opening)
            result.append(chr(10) + chr(96)*3 + (language.group(1) if language else "")
                          + chr(10) + remaining[:-len("</CodeBlock>")]
                          + chr(10) + chr(96)*3 + chr(10))
        else:
            result.append(raw)
        end = fence.end()
    result.extend(flatten(chunk) if n % 2 == 0 else chunk
                  for n, chunk in enumerate(INLINE.split(text[end:])))
    return ''.join(result)
