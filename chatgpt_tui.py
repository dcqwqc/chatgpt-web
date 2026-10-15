"""Fullscreen terminal interface for chatgpt-web."""

from __future__ import annotations

import asyncio
from io import StringIO
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.styles import Style
from prompt_toolkit.data_structures import Point
from prompt_toolkit.widgets import TextArea

from image_backend import generate_image
import local_history
from cooldown import retry_after_seconds, remaining_seconds, format_remaining, display_rate_limit
from rich.console import Console
from rich.markdown import BlockQuote, CodeBlock, Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.segment import Segment
from rich.style import Style as RichStyle
from rich.syntax import Syntax
from rich.text import Text
from image_preview import render_preview
from terminal_native import supports_native, render_native
from kitty_placeholders import prepare_virtual_image, placeholder_rows, id_to_rgb
from rich.theme import Theme


Message = dict[str, Any]
LoadConversation = Callable[[str], dict[str, Any]]
ListConversations = Callable[[], list[dict[str, Any]]]
SendPrompt = Callable[
    [
        str | None,
        str,
        list[dict[str, Any]],
        Callable[[dict[str, Any]], None],
        Callable[[Any], None],
    ],
    dict[str, Any],
]
UploadImage = Callable[[bytes, str, str, int, int], dict[str, Any]]
RenameConversation = Callable[[str, str], None]
SetActiveConversation = Callable[[str | None], None]

ANNOTATION_PATTERN = re.compile(r"\ue200([^\ue201\ue202]+)(?:\ue202(.*?))?\ue201")
FILE_CITATION_PATTERN = re.compile(
    r"[ \t]*\[F\d+:L\d+(?:[ \t]*[-–—][ \t]*L?\d+)?\]"
)
PROMPT_BACKGROUND_TOKEN = "48;2;38;37;35m"
PROMPT_BACKGROUND_SGR = "\x1b[48;2;38;37;35m"
TRAILING_STYLED_SPACES_PATTERN = re.compile(
    r"(?:\x1b\[[0-9;]*m[ \t]+\x1b\[0m)+(?=\n)"
)
WRITING_DIRECTIVE_PATTERN = re.compile(
    r'(?m)^[\ue200]?\s*:::writing\{(?P<attributes>[^}\n]*)\}\s*$'
)

MARKDOWN_THEME = Theme({
    "markdown.text": "#f2f0ec",
    "markdown.paragraph": "#f2f0ec",
    "markdown.h1": "bold #f8f6f2",
    "markdown.h2": "bold #f8f6f2",
    "markdown.h3": "bold #f8f6f2",
    "markdown.h4": "bold #f2f0ec",
    "markdown.h5": "bold #d9d6d0",
    "markdown.h6": "bold #d9d6d0",
    "markdown.link": "underline #c4a7e7",
    "markdown.link_url": "underline #c4a7e7",
    "markdown.code": "#c4a7e7",
    "markdown.block_quote": "#b8b4ad",
    "markdown.hr": "#55514b",
    "markdown.list": "#f2f0ec",
    "markdown.item.bullet": "#f2f0ec",
    "markdown.item.number": "#f2f0ec",
})


def detect_code_language(code: str) -> str:
    """Choose a useful lexer for common unlabelled Markdown code blocks."""
    stripped = code.lstrip()
    if re.search(r"^(?:from\s+\w+\s+import|import\s+\w+|def\s+\w+|class\s+\w+)", stripped, re.M):
        return "python"
    if re.search(r"^(?:use\s+(?:std|crate)::|(?:pub\s+)?fn\s+\w+|impl(?:<.*?>)?\s+\w+)", stripped, re.M):
        return "rust"
    if re.search(r"\b(?:const|let|var)\s+\w+\s*=|=>|console\.\w+\(", code):
        return "javascript"
    if re.search(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE TABLE)\b", code, re.I | re.M):
        return "sql"
    if stripped.startswith(("#!/bin/sh", "#!/usr/bin/env bash", "#!/bin/bash")):
        return "bash"
    if stripped.startswith(("{", "[")):
        try:
            import json

            json.loads(stripped)
            return "json"
        except ValueError:
            pass
    if re.search(r"</?[A-Za-z][^>]*>", code):
        return "html"
    if re.search(r"^\s*#include\s*[<\"]|\b(?:int|void|char)\s+\w+\s*\([^)]*\)\s*\{", code, re.M):
        return "c"
    return "text"


class HighlightedCodeBlock(CodeBlock):
    def __rich_console__(self, console: Console, options: Any) -> Any:
        code = str(self.text).rstrip()
        lexer = self.lexer_name
        if lexer == "text":
            lexer = detect_code_language(code)
        yield Syntax(code, lexer, theme=self.theme, word_wrap=True, padding=1)


class CompactBlockQuote(BlockQuote):
    """Render quotes as a narrow rail instead of a full-width block."""

    def __rich_console__(self, console: Console, options: Any) -> Any:
        quote_options = options.update(width=max(1, options.max_width - 4))
        lines = console.render_lines(self.elements, quote_options, style=self.style)
        rail = Segment("│ ", RichStyle(color="#98938c", dim=True))
        for line in lines:
            yield rail
            yield from line
            yield Segment("\n")


class ChatMarkdown(Markdown):
    elements = {
        **Markdown.elements,
        "blockquote_open": CompactBlockQuote,
        "fence": HighlightedCodeBlock,
        "code_block": HighlightedCodeBlock,
    }


class PromptMarkdown:
    """Markdown content with a Codex-style prompt marker on its first line."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __rich_console__(self, console: Console, options: Any) -> Any:
        content_options = options.update(width=max(1, options.max_width - 2))
        lines = console.render_lines(
            ChatMarkdown(self.text, code_theme="monokai"),
            content_options,
            pad=False,
        )
        for index, line in enumerate(lines):
            marker = "› " if index == 0 else "  "
            style = RichStyle(color="#c4a7e7", bold=True) if index == 0 else None
            yield Segment(marker, style)
            yield from line
            yield Segment("\n")


class SlashCommandCompleter(Completer):
    COMMANDS = (
        ("/new", "start a new conversation"),
        ("/resume", "search CLI Project conversations"),
        ("/model", "select from available ChatGPT models"),
        ("/usage", "view current ChatGPT Web usage status"),
        ("/history", "reload this conversation"),
        ("/rename", "rename this conversation"),
        ("/copy", "copy the latest response"),
        ("/image", "generate an image from a prompt"),
        ("/edit", "edit an image: PATH | prompt"),
        ("/animate", "generate an animated WebP"),
        ("/preview", "show an image directly in the terminal"),
        ("/hd", "view the last image as true pixels"),
        ("/open", "open original image at full resolution"),
        ("/remove", "remove the latest image"),
        ("/clear", "clear the displayed transcript"),
        ("/help", "show available commands"),
        ("/exit", "quit chatgpt-web"),
        ("/quit", "quit chatgpt-web"),
    )

    def get_completions(self, document: Any, complete_event: Any) -> Any:
        token = document.text_before_cursor
        if not token.startswith("/") or any(char.isspace() for char in token):
            return
        query = token.casefold()
        for command, description in self.COMMANDS:
            if command.startswith(query) and command != query:
                yield Completion(
                    command,
                    start_position=-len(token),
                    display=command,
                    display_meta=description,
                )


class ComposerPlaceholderProcessor(Processor):
    def apply_transformation(self, transformation_input: Any) -> Transformation:
        fragments = transformation_input.fragments
        if transformation_input.document.text or transformation_input.lineno != 0:
            return Transformation(fragments)
        return Transformation(
            fragments + [("class:placeholder", "Ask ChatGPT anything")],
            source_to_display=lambda position: position,
            display_to_source=lambda position: 0,
        )


class TranscriptWindow(Window):
    """A Window with the multi-line wheel movement users expect in terminals."""

    def __init__(self, *args: Any, on_manual_scroll: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.on_manual_scroll = on_manual_scroll

    def _scroll_up(self) -> None:
        info = self.render_info
        if info is None:
            return
        self.on_manual_scroll()
        self.vertical_scroll = max(0, info.vertical_scroll - 4)

    def _scroll_down(self) -> None:
        info = self.render_info
        if info is None:
            return
        self.on_manual_scroll()
        last_scroll = max(0, info.content_height - info.window_height)
        self.vertical_scroll = min(last_scroll, info.vertical_scroll + 4)


def render_chatgpt_annotations(text: str) -> str:
    """Turn ChatGPT web's private-use annotations into terminal-safe text."""

    hidden_citation = "\x00"

    writing_directive = WRITING_DIRECTIVE_PATTERN.search(text)
    if writing_directive:
        attributes = writing_directive.group("attributes")
        title_match = re.search(r'\btitle="([^"]+)"', attributes)
        title = title_match.group(1) if title_match else "Document"
        text = WRITING_DIRECTIVE_PATTERN.sub(f"**{title}**", text, count=1)
        # The web client treats the matching container delimiter as metadata.
        text = re.sub(r"(?m)^\s*:::\s*$", "", text)

    def replace(match: re.Match[str]) -> str:
        kind = match.group(1).casefold()
        if kind == "filecite":
            return hidden_citation
        if kind == "cite":
            return hidden_citation
        # Image groups, navigation lists, entities, and other web-only widgets
        # have no useful terminal payload in the marker itself.
        return ""

    rendered = ANNOTATION_PATTERN.sub(replace, text)
    def remove_hidden_citation(match: re.Match[str]) -> str:
        left = rendered[match.start() - 1] if match.start() else "\n"
        right = rendered[match.end()] if match.end() < len(rendered) else "\n"
        return " " if left != "\n" and right != "\n" else ""

    rendered = re.sub(
        rf"[ \t]*{re.escape(hidden_citation)}[ \t]*",
        remove_hidden_citation,
        rendered,
    )
    # File citations are useful only when the client can resolve their hidden
    # source metadata. Keep them in stored messages but omit them from display.
    rendered = FILE_CITATION_PATTERN.sub("", rendered)
    # Some ChatGPT Web conversation nodes escape every Markdown delimiter in
    # otherwise complete Markdown. Only unescape after detecting a fenced block
    # so intentional backslashes in ordinary prose and source code survive.
    if re.search(r"(?m)^\s*\\`\\`\\`", rendered):
        rendered = re.sub(r"\\([`*_{}\[\]()#+.!>|~-])", r"\1", rendered)
    # Streaming can end between the opening marker and its terminator. Hide the
    # incomplete suffix until the next delta completes it.
    rendered = re.sub(r"\ue200[^\ue201]*$", "", rendered)
    rendered = rendered.replace("\ue200", "").replace("\ue201", "").replace("\ue202", "")
    rendered = re.sub(r"[ \t]+([,.;:!?])", r"\1", rendered)
    return rendered


def visible_chat_message(message: dict[str, Any]) -> Message | None:
    """Return user-facing turns while excluding web tool protocol nodes."""
    role = message.get("role")
    text = message.get("text")
    content_type = message.get("content_type")
    if role == "user":
        visible = content_type in (None, "text")
    elif role == "assistant":
        visible = (
            content_type in (None, "text")
            and message.get("channel") in (None, "commentary", "final")
            and message.get("recipient") in (None, "all")
        )
    else:
        visible = False
    if not visible or not isinstance(text, str) or not text:
        return None
    return {"role": role, "text": text}


class ChatTui:
    def __init__(
        self,
        list_conversations: ListConversations,
        load_conversation: LoadConversation,
        send_prompt: SendPrompt,
        conversation_id: str | None = None,
        title: str = "New conversation",
        initial_messages: list[dict[str, Any]] | None = None,
        model: str | None = None,
        upload_image: UploadImage | None = None,
        rename_conversation: RenameConversation | None = None,
        set_active_conversation: SetActiveConversation | None = None,
        list_models: Callable[[], dict[str, Any]] | None = None,
        select_model: Callable[[str], dict[str, Any]] | None = None,
        get_usage: Callable[[], dict[str, Any]] | None = None,
        local_session_id: str | None = None,
        last_image: str | None = None,
    ) -> None:
        self.list_conversations = list_conversations
        self.load_conversation = load_conversation
        self.send_prompt = send_prompt
        self.upload_image = upload_image
        self.rename_conversation = rename_conversation
        self.set_active_conversation = set_active_conversation
        self.list_models = list_models
        self.select_model = select_model
        self.get_usage = get_usage
        self.conversation_id = conversation_id
        self.legacy_read_only = False
        self.local_session_id = local_session_id or local_history.new_id()
        self.title = title
        self.model = model or os.environ.get("CHATGPT_WEB_MODEL", "gpt-6")
        self.messages: list[Message] = []
        for message in initial_messages or []:
            if (isinstance(message, dict) and
                message.get("role") in ("user", "assistant", "system", "image")
                and isinstance(message.get("text"), str)):
                restored = {"role": message["role"], "text": message["text"]}
                until = message.get("rate_limit_until")
                if isinstance(until, (int, float)):
                    restored["rate_limit_until"] = until
                self.messages.append(restored)
            else:
                visible = visible_chat_message(message)
                if visible is not None:
                    self.messages.append(visible)
        self.rate_limit_until: float | None = max((
            m["rate_limit_until"] for m in self.messages
            if isinstance(m.get("rate_limit_until"), (int, float))
            and remaining_seconds(m["rate_limit_until"]) > 0
        ), default=None)
        self.busy = False
        self.loading_conversation = False
        self.loading_started: float | None = None
        self.load_on_start = conversation_id is not None and not initial_messages
        self.image_busy = False
        self.image_started: float | None = None
        self.last_generated_image: str | None = last_image or next(
            (m["text"] for m in reversed(self.messages)
             if m["role"] == "image" and Path(m["text"]).expanduser().is_file()),
            None
        )
        self.native_image_preview = (
            supports_native()
            and os.environ.get("CHATGPT_WEB_IMAGE_MODE", "native").lower()
                not in ("ansi", "blocks", "off", "fallback")
        )
        self.virtual_image_registry: dict[str, tuple[int, int, int]] = {}
        self.virtual_image_lock = threading.RLock()
        self.uploading_image = False
        self.upload_started: float | None = None
        self.pending_attachments: list[dict[str, Any]] = []
        self.status = "Ready"
        self.cancel_connection: Any = None
        self.lock = threading.RLock()
        self.resume_visible = False
        self.resume_loading = False
        self.resume_error: str | None = None
        self.resume_items: list[dict[str, Any]] = []
        self.resume_filtered: list[dict[str, Any]] = []
        self.resume_index = 0
        self.resume_filter = Condition(lambda: self.resume_visible)
        self.model_visible = False
        self.model_loading = False
        self.model_items: list[dict[str, str]] = []
        self.model_filtered: list[dict[str, str]] = []
        self.model_index = 0
        self.model_pending_steps = 0
        self.model_error: str | None = None
        self.model_filter = Condition(lambda: self.model_visible)
        self.commands_filter = Condition(
            lambda: not self.resume_visible and not self.model_visible and self.input.text.startswith("/")
            and '\n' not in self.input.text
        )
        self.follow_output = True
        self.transcript_line_count = 1
        self.active_started: float | None = None
        self.last_elapsed: float | None = None
        self.render_cache: dict[tuple[str, str, int, bool], str] = {}
        self.formatted_transcript_source = ""
        self.formatted_transcript: ANSI = ANSI("")
        self.committed_message_count = 0
        self.history_tail = ""
        self.event_loop: asyncio.AbstractEventLoop | None = None
        self.last_terminal_size: tuple[int, int] | None = None
        self.resize_reflowing = False
        self.resize_reflow_pending = False

        self.transcript_control = FormattedTextControl(
            text=self.render_formatted_transcript,
            focusable=True,
            get_cursor_position=self.transcript_cursor_position,
        )
        self.transcript_window = TranscriptWindow(
            content=self.transcript_control,
            wrap_lines=True,
            right_margins=[],
            allow_scroll_beyond_bottom=False,
            on_manual_scroll=self.pause_following,
            style="class:transcript",
        )
        self.history_entries: list[str] = self.load_prompt_history()
        self.history_position = -1
        self.history_draft = ""
        self.input = TextArea(
            height=D(min=1, max=6),
            multiline=True,
            wrap_lines=True,
            prompt=self.render_prompt,
            dont_extend_height=True,
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
            input_processors=[ComposerPlaceholderProcessor()],
            style="class:composer",
        )
        self.input.buffer.on_text_changed += self.on_input_changed
        self.resume_search = TextArea(
            height=1,
            multiline=False,
            prompt="  Search  ",
            style="class:picker.search",
        )
        self.resume_search.buffer.on_text_changed += lambda _buffer: self.filter_resume()
        self.model_search = TextArea(height=1, multiline=False, prompt="  Filter  ", style="class:picker.search")
        self.model_search.buffer.on_text_changed += lambda _buffer: self.filter_models()
        self.model_list_window = Window(
            content=FormattedTextControl(self.render_model_list, focusable=True),
            wrap_lines=False, always_hide_cursor=True, height=D.exact(7),
            style="class:picker.list",
        )
        self.resume_list_control = FormattedTextControl(
            text=self.render_resume_list,
            focusable=True,
        )
        self.resume_list_window = Window(
            content=self.resume_list_control,
            wrap_lines=False,
            always_hide_cursor=True,
            height=D.exact(7),
            style="class:picker.list",
        )
        self.bindings = self.make_bindings()
        # Command hints and conversation selection stay in normal document flow.
        # Unlike Float/Frame they never cover the transcript with a full-height modal.
        command_hints = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(self.render_command_hints),
                height=D.exact(5),
                style="class:commands",
            ),
            filter=self.commands_filter,
        )
        resume_picker = ConditionalContainer(
            content=HSplit([
                Window(
                    content=FormattedTextControl(
                        [("class:picker.title", "  RECENT CONVERSATIONS"),
                         ("class:picker.hint", "     Search · ↑ ↓ · Enter · Esc")]),
                    height=1,
                    style="class:picker.header",
                ),
                self.resume_search,
                self.resume_list_window,
                Window(
                    content=FormattedTextControl(
                        "  Ctrl+R  conversations    Esc  close"
                    ),
                    height=1,
                    style="class:picker.help",
                ),
            ]),
            filter=self.resume_filter,
        )
        model_picker = ConditionalContainer(
            content=HSplit([
                Window(content=FormattedTextControl([
                    ("class:picker.title", "  SELECT CHATGPT MODEL"),
                    ("class:picker.hint", "    Filter · ↑ ↓ · Enter · Esc")]),
                    height=1, style="class:picker.header"),
                self.model_search,
                self.model_list_window,
                Window(content=FormattedTextControl("  Enter  select    Esc  close"),
                       height=1, style="class:picker.help"),
            ]),
            filter=self.model_filter,
        )
        root = HSplit(
            [
                self.transcript_window,
                Window(
                    height=1,
                    content=FormattedTextControl(self.render_activity),
                    style="class:activity",
                ),
                Window(height=1, char=" ", style="class:composer"),
                self.input,
                command_hints,
                resume_picker,
                model_picker,
                Window(height=1, char=" ", style="class:composer"),
                Window(
                    height=1,
                    content=FormattedTextControl(self.render_metadata),
                    style="class:metadata",
                ),
            ],
            height=self.viewport_height,
        )
        self.app: Application[Any] = Application(
            layout=Layout(root, focused_element=self.input),
            key_bindings=self.bindings,
            full_screen=False,
            # Completed turns are written into the terminal's scrollback. Let
            # the terminal keep wheel events so that history remains reachable.
            mouse_support=False,
            min_redraw_interval=0.05,
            max_render_postpone_time=0.1,
            before_render=self.detect_terminal_resize,
            style=Style.from_dict({
                "transcript": "bg:#151413 #f1efeb",
                "separator": "#56514b",
                "activity": "bg:#151413 #c4bfb6",
                "composer": "bg:#26222c #f9f7f2",
                "prompt": "bg:#26222c bold #c4a7e7",
                "placeholder": "bg:#26222c #a8a39c",
                "metadata": "bg:#1c1b19 #c5c0b8",
                "metadata.model": "bg:#1c1b19 bold #f2eee6",
                "metadata.ready": "bg:#1c1b19 bold #a6d1ad",
                "metadata.rate": "bg:#1c1b19 bold #e3b2cd",
                "commands": "bg:#21201e #f1ede7",
                "commands.key": "bg:#21201e bold #c4a7e7",
                "commands.description": "bg:#21201e #d0cac1",
                "picker.header": "bg:#21201e #f4f1eb",
                "picker.title": "bg:#21201e bold #f8f5ee",
                "picker.hint": "bg:#21201e #c1bbb1",
                "picker.search": "bg:#34312e #ffffff",
                "picker.list": "bg:#21201e #f3efe9",
                "picker.help": "bg:#21201e #bfb8ac",
                "resume.selected": "bg:#4c3b62 #ffffff bold",
                "resume.item": "bg:#21201e #f3efe9",
                "resume.time": "bg:#21201e #c0bab0",
            }),
        )

    async def animate_activity(self) -> None:
        """Refresh animated status only while an operation is active.

        A permanent prompt_toolkit refresh interval repaints the terminal even
        when the UI is idle. Those writes clear native terminal selections,
        making copied text lose its highlight almost immediately.
        """
        previous_remaining = self.cooldown_remaining()
        while True:
            await asyncio.sleep(0.12)
            remaining = self.cooldown_remaining()
            if remaining != previous_remaining:
                previous_remaining = remaining
                self.app.invalidate()
                if not remaining:
                    if self.status.startswith(("Rate limit", "Rate limited")):
                        self.status = "Ready"
                    if not self.busy and self.has_pending_messages():
                        # Rebuild once instead of appending a second copy of the
                        # old terminal scrollback when the countdown expires.
                        with self.lock:
                            self.committed_message_count = len(self.messages)
                        self.reflow_committed_history()
            if self.busy or self.loading_conversation or self.uploading_image or self.image_busy:
                self.app.invalidate()

    def render_prompt(self) -> FormattedText:
        return FormattedText([("class:prompt", " › ")])

    def viewport_height(self) -> D:
        try:
            rows = self.app.output.get_size().rows
        except Exception:
            rows = shutil.get_terminal_size((100, 24)).lines
        return D.exact(max(6, rows))

    @staticmethod
    def format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        if total < 60:
            return f"{total}s"
        return f"{total // 60}m {total % 60:02d}s"

    def render_activity(self) -> FormattedText:
        started = (
            self.active_started if self.busy
            else self.loading_started if self.loading_conversation
            else self.image_started if self.image_busy
            else self.upload_started
        )
        if started is not None:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = frames[int((time.monotonic() - started) * 10) % len(frames)]
            elapsed = self.format_elapsed(time.monotonic() - started)
            return FormattedText([("", f" {frame} {self.status} · {elapsed} ")])
        cooldown = self.cooldown_remaining()
        if cooldown:
            return FormattedText([("class:metadata.rate",
                f" • Rate limited · retry in {format_remaining(cooldown)} · draft kept ")])
        if self.status not in ("Ready", ""):
            return FormattedText([("", f" • {self.status} ")])
        if self.pending_attachments:
            names = ", ".join(item["name"] for item in self.pending_attachments)
            return FormattedText([("", f" 📎 {names} · /remove to detach ")])
        if self.last_elapsed is not None:
            return FormattedText([
                ("", f" — Worked for {self.format_elapsed(self.last_elapsed)} "),
            ])
        return FormattedText([("", "")])

    def cooldown_remaining(self) -> int:
        return remaining_seconds(self.rate_limit_until)

    def has_pending_messages(self) -> bool:
        with self.lock:
            return self.committed_message_count < len(self.messages)

    def render_metadata(self) -> FormattedText:
        cwd = str(Path.cwd())
        home = str(Path.home())
        if cwd == home or cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home):]
        cooldown = self.cooldown_remaining()
        state = (
            "streaming" if self.busy else
            "loading" if self.loading_conversation else
            "uploading" if self.uploading_image else
            "generating" if self.image_busy else
            "rate limited" if cooldown else "ready"
        )
        title = self.title if len(self.title) <= 34 else self.title[:31] + "…"
        model_text = f" {self.model} "
        state_text = f"{state} "
        context = f"· CLI Project · {title} · {cwd} "
        width = self.transcript_width() + 3
        if not cooldown:
            return FormattedText([
                ("class:metadata.model", model_text),
                ("class:metadata.ready", state_text),
                ("class:metadata", context),
            ])
        badge = f" RATE LIMIT · {format_remaining(cooldown)} "
        left_width = max(0, width - len(badge))
        if len(model_text + state_text + context) > left_width:
            context = context[:max(0, left_width - len(model_text + state_text))]
        padding = max(1, width - len(model_text + state_text + context + badge))
        return FormattedText([
            ("class:metadata.model", model_text),
            ("class:metadata.rate", state_text),
            ("class:metadata", context + " " * padding),
            ("class:metadata.rate", badge),
        ])

    def render_transcript(self) -> str:
        with self.lock:
            messages = [
                dict(message)
                for message in self.messages[self.committed_message_count:]
            ]
        width = self.transcript_width()
        if not messages:
            if self.committed_message_count:
                self.transcript_line_count = self.history_tail.count("\n") + 1
                return self.history_tail
            buffer = StringIO()
            console = Console(
                file=buffer,
                force_terminal=True,
                color_system="truecolor",
                no_color=False,
                width=width,
                theme=MARKDOWN_THEME,
            )
            console.print(Text("Start a conversation below.", style="dim"))
            rendered = buffer.getvalue()
        else:
            pieces = [self.history_tail] if self.history_tail else []
        for index, message in enumerate(messages):
            role = message["role"]
            text = render_chatgpt_annotations(self.present_message(message))
            active = self.busy and index == len(messages) - 1 and role == "assistant"
            dynamic = remaining_seconds(message.get("rate_limit_until")) > 0
            separated = index > 0 or bool(self.history_tail)
            key = (role, text, width, separated)
            if active or dynamic or key not in self.render_cache:
                value = self.render_message(role, text, width, separated, active)
                if not active:
                    if len(self.render_cache) > 256:
                        self.render_cache.clear()
                    self.render_cache[key] = value
            else:
                value = self.render_cache[key]
            pieces.append(value)
        if messages:
            rendered = "".join(pieces)
        self.transcript_line_count = rendered.count("\n") + 1
        return rendered

    def transcript_width(self) -> int:
        try:
            return max(40, self.app.output.get_size().columns - 3)
        except Exception:
            return max(40, shutil.get_terminal_size((100, 24)).columns - 3)

    def detect_terminal_resize(self, _app: Application[Any]) -> None:
        """Replay committed history when terminal dimensions change."""
        try:
            size = self.app.output.get_size()
            current = (size.columns, size.rows)
        except Exception:
            fallback = shutil.get_terminal_size((100, 24))
            current = (fallback.columns, fallback.lines)
        if self.last_terminal_size is None:
            self.last_terminal_size = current
            return
        if current == self.last_terminal_size:
            return
        self.last_terminal_size = current
        if self.resize_reflowing:
            self.resize_reflow_pending = True
            return
        if self.event_loop is not None:
            self.event_loop.call_soon(self.reflow_committed_history)

    def reflow_committed_history(self) -> None:
        if self.resize_reflowing:
            self.resize_reflow_pending = True
            return
        self.resize_reflowing = True
        with self.lock:
            completed = [
                dict(message)
                for message in self.messages[:self.committed_message_count]
            ]
        rendered = self.render_history(completed) if completed else ""
        committed = self.partition_history(rendered)
        self.formatted_transcript_source = ""

        def replay() -> None:
            self.reset_terminal_session()
            self.reload_virtual_placements(completed)
            self.write_terminal_history(committed)

        future = run_in_terminal(replay, render_cli_done=False)

        def finished(_future: Any) -> None:
            self.resize_reflowing = False
            self.scroll_bottom()
            if self.resize_reflow_pending:
                self.resize_reflow_pending = False
                if self.event_loop is not None:
                    self.event_loop.call_soon(self.reflow_committed_history)

        future.add_done_callback(finished)

    def present_message(self, message: Message) -> str:
        return display_rate_limit(
            message["text"], message.get("rate_limit_until")
        )

    def render_history(self, messages: list[Message]) -> str:
        width = self.transcript_width()
        return "".join(
            self.render_message(
                message["role"],
                render_chatgpt_annotations(self.present_message(message)),
                width,
                index > 0,
                False,
            )
            for index, message in enumerate(messages)
        )

    @staticmethod
    def write_terminal_history(rendered: str) -> None:
        sys.stdout.write(rendered)
        sys.stdout.flush()

    @staticmethod
    def reset_terminal_session() -> None:
        # Clear the visible screen and purge earlier terminal scrollback while
        # staying on the main screen. This makes launch the history boundary.
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        sys.stdout.flush()

    def partition_history(self, rendered: str) -> str:
        """Keep the newest viewport live and return older lines to commit."""
        transcript_rows = max(1, self.viewport_height().preferred - 5)
        lines = rendered.splitlines(keepends=True)
        split = max(0, len(lines) - transcript_rows)
        self.history_tail = "".join(lines[split:])
        return self.stabilize_prompt_backgrounds("".join(lines[:split]))

    @staticmethod
    def stabilize_prompt_backgrounds(rendered: str) -> str:
        """Fill prompt rows without relying on discardable trailing spaces."""
        lines = []
        for line in rendered.splitlines(keepends=True):
            if PROMPT_BACKGROUND_TOKEN in line:
                line = TRAILING_STYLED_SPACES_PATTERN.sub(
                    PROMPT_BACKGROUND_SGR + "\x1b[K\x1b[0m",
                    line,
                )
            lines.append(line)
        return "".join(lines)

    def commit_initial_history(self) -> None:
        with self.lock:
            messages = [dict(message) for message in self.messages]
        if not messages:
            return
        if self.cooldown_remaining():
            self.committed_message_count = 0
            return
        rendered = self.render_history(messages)
        committed = self.partition_history(rendered)
        self.write_terminal_history(committed)
        self.committed_message_count = len(messages)

    def commit_loaded_history(self, messages: list[Message]) -> None:
        if self.cooldown_remaining():
            self.committed_message_count = 0
            self.loading_conversation = False
            self.loading_started = None
            self.scroll_bottom()
            return
        rendered = self.render_history(messages)
        committed = self.partition_history(rendered)
        self.committed_message_count = len(messages)

        def schedule_write() -> None:
            def replay() -> None:
                self.reset_terminal_session()
                self.reload_virtual_placements(messages)
                self.write_terminal_history(committed)

            future = run_in_terminal(
                replay,
                render_cli_done=False,
            )

            def finished(_future: Any) -> None:
                self.loading_conversation = False
                self.loading_started = None
                self.status = "Ready"
                self.scroll_bottom()

            future.add_done_callback(finished)

        if self.event_loop is not None:
            self.event_loop.call_soon_threadsafe(schedule_write)

    def commit_completed_turns(self) -> None:
        if self.cooldown_remaining():
            self.app.invalidate()
            return
        with self.lock:
            end = len(self.messages)
            messages = [
                dict(message)
                for message in self.messages[self.committed_message_count:end]
            ]
            # Remove completed turns from the live layout immediately. Rich
            # rendering and terminal output happen off the input event loop.
            self.committed_message_count = end
        if not messages:
            return
        rendered = self.render_history(messages)
        committed = self.partition_history(self.history_tail + rendered)

        def schedule_write() -> None:
            future = run_in_terminal(
                lambda: self.write_terminal_history(committed),
                render_cli_done=False,
            )
            future.add_done_callback(lambda _future: self.scroll_bottom())

        if self.event_loop is not None:
            self.event_loop.call_soon_threadsafe(schedule_write)

    def render_formatted_transcript(self) -> ANSI:
        rendered = self.render_transcript()
        if rendered != self.formatted_transcript_source:
            self.formatted_transcript_source = rendered
            self.formatted_transcript = ANSI(rendered)
        return self.formatted_transcript

    def reload_virtual_placements(self, messages: list[Message]) -> None:
        """After a terminal screen clear, repopulate its image registry."""
        if not self.native_image_preview or not sys.stdout.isatty():
            return
        self.virtual_image_registry.clear()
        for message in messages:
            if message.get("role") != "image":
                continue
            try:
                self.register_virtual_image(str(message["text"]), self.transcript_width())
            except (OSError, ValueError, RuntimeError):
                continue

    def register_virtual_image(self, path: str, width: int) -> tuple[int, int, int]:
        """Upload pixel data as a virtual placement (no cursor movement).

        Kitty/Ghostty use the colored Unicode cells to position this image.
        The cells themselves survive prompt_toolkit and scrollback redraws.
        """
        source = Path(path).expanduser().resolve()
        identifier, columns, rows, packets = prepare_virtual_image(source, width)
        key = str(source)
        current = (identifier, columns, rows)
        with self.virtual_image_lock:
            if self.virtual_image_registry.get(key) != current:
                output = sys.stdout.buffer
                for packet in packets:
                    output.write(packet)
                output.flush()
                self.virtual_image_registry[key] = current
        return current

    def render_message(
        self,
        role: str,
        text: str,
        width: int,
        separated: bool,
        active: bool,
    ) -> str:
        buffer = StringIO()
        console = Console(
            file=buffer,
            force_terminal=True,
            color_system="truecolor",
            no_color=False,
            width=width,
            soft_wrap=False,
            theme=MARKDOWN_THEME,
        )
        if separated and role == "user":
            console.print(Rule(style="#56514b on #151413"))
        if role == "user":
            console.print(
                Padding(
                    PromptMarkdown(text),
                    (1, 2),
                    style="#f4f0f8 on #26222c",
                    expand=True,
                )
            )
        elif role == "assistant":
            content = ChatMarkdown(text, code_theme="monokai") if text else Text(
                "Thinking…" if active else "", style="italic #c4bfb6"
            )
            console.print(Padding(content, (0, 2), style="#f2f0ec on #151413", expand=True))
        elif role == "image":
            source = Path(text).expanduser()
            path = source.resolve()
            # Prefer full-pixel Kitty images in Ghostty/Kitty. ANSI half-blocks
            # are the cross-terminal fallback, not the default on Mirai.
            displayed_native = False
            if self.native_image_preview and sys.stdout.isatty():
                try:
                    image_id, columns, rows = self.register_virtual_image(str(path), width)
                    red, green, blue = id_to_rgb(image_id)
                    color = f"#{red:02x}{green:02x}{blue:02x} on #151413"
                    for unicode_row in placeholder_rows(image_id, columns, rows):
                        console.print(Padding(
                            Text(unicode_row, style=color, no_wrap=True),
                            (0, 2), expand=True,
                            style="#f2f0ec on #151413",
                        ))
                    displayed_native = True
                except (OSError, ValueError, RuntimeError) as error:
                    console.print(Text(
                        f"  Native image unavailable ({error}); showing fallback",
                        style="#c4a7e7",
                    ))
            if not displayed_native:
                try:
                    preview = render_preview(str(path),
                        max_columns=max(10, min(88, width - 7)),
                        max_rows=26)
                    console.print(Padding(
                        Text.from_ansi(preview), (0, 2),
                        style="#f2f0ec on #151413", expand=True,
                    ))
                except (OSError, ValueError, ImportError) as error:
                    console.print(Padding(Text(
                        f"Image preview unavailable: {error}", style="#c4a7e7"
                    ), (0, 2), style="#f2f0ec on #151413", expand=True))
            # This explicit path is always present—even if the terminal cannot
            # render a preview or the original image was generated via chat.
            if path.is_file():
                caption = Text()
                caption.append("  Saved original: ", style="#bfb8c9")
                caption.append(path.as_uri(), style="underline #c4a7e7")
                console.print(Padding(caption, (0, 2),
                    style="#f2f0ec on #151413", expand=True))
                console.print(Padding(Text(
                    "  Open full resolution with /open", style="#c4a7e7"
                ), (0, 2), style="#f2f0ec on #151413", expand=True))
        else:
            console.print(Padding(ChatMarkdown(text, code_theme="monokai"),
                (0, 2), style="#c4a7e7 on #151413", expand=True))
        console.print(Text(" " * width, style="#f2f0ec on #151413"))
        return buffer.getvalue()

    def transcript_cursor_position(self) -> Point:
        if self.follow_output:
            return Point(x=0, y=max(0, self.transcript_line_count - 1))
        return Point(x=0, y=max(0, self.transcript_window.vertical_scroll))

    def render_resume_list(self) -> FormattedText:
        if self.resume_loading:
            return FormattedText([("class:resume.time", " Loading conversations…")])
        if self.resume_error:
            return FormattedText([("class:resume.time", f"  Could not load chats: {self.resume_error[:90]}")])
        if not self.resume_filtered:
            return FormattedText([("class:resume.time", " No matching conversations")])
        fragments: list[tuple[str, str]] = []
        for index, item in enumerate(self.resume_filtered):
            title = item.get("title") or "Untitled"
            updated = item.get("update_time")
            stamp = ""
            if isinstance(updated, (int, float)):
                try:
                    stamp = datetime.fromtimestamp(updated).strftime("%Y-%m-%d %H:%M")
                except (OSError, OverflowError, ValueError):
                    pass
            marker = "›" if index == self.resume_index else " "
            style = "class:resume.selected" if index == self.resume_index else "class:resume.item"
            fragments.append((style, f"  {marker} {str(title).replace(chr(10), chr(32))[:max(20, self.transcript_width() - 26)]}  "))
            fragments.append(("class:resume.time", f"{stamp}\n"))
        return FormattedText(fragments)

    def render_model_list(self) -> FormattedText:
        if self.model_loading:
            return FormattedText([("class:resume.time", "  Fetching models…")])
        if self.model_error:
            return FormattedText([("class:resume.time", f"  Failed: {self.model_error[:80]}")])
        if not self.model_filtered:
            return FormattedText([("class:resume.time", "  No matching models")])
        count = len(self.model_filtered)
        visible_rows = 7
        # Never rely on prompt_toolkit's automatic scroll: focus is on the
        # search input, so selecting off-screen models used to appear frozen.
        start = min(max(0, self.model_index - visible_rows // 2),
                    max(0, count - visible_rows))
        fragments: list[tuple[str, str]] = []
        for index in range(start, min(count, start + visible_rows)):
            item = self.model_filtered[index]
            marker = "›" if index == self.model_index else " "
            style = "class:resume.selected" if index == self.model_index else "class:resume.item"
            current = "●" if item["slug"] == self.model else " "
            fragments.append((style, f"  {marker}{current} {item['slug'][:24]:<24} {item['title'][:28]}" + chr(10)))
        return FormattedText(fragments)

    def filter_models(self) -> None:
        query = self.model_search.text.strip().casefold()
        self.model_filtered = [
            model for model in self.model_items
            if query in model["slug"].casefold() or query in model["title"].casefold()
        ]
        self.model_index = min(self.model_index, max(0, len(self.model_filtered)-1))
        if hasattr(self, "app"): self.app.invalidate()

    def open_models(self) -> None:
        if self.busy or self.model_visible or self.resume_visible:
            return
        if self.list_models is None:
            self.status = "Model selection unavailable"
            self.app.invalidate()
            return
        self.model_visible = True
        self.model_loading = True
        self.model_error = None
        self.model_items = []
        self.model_filtered = []
        self.model_index = 0
        self.model_pending_steps = 0
        self.model_search.text = ""
        self.app.layout.focus(self.model_search)
        self.app.invalidate()

        def fetch() -> None:
            try:
                status = self.list_models()
                self.model_items = status.get("models", [])
                self.model = status.get("selected") or self.model
                self.filter_models()
                self.model_index = next(
                    (i for i, item in enumerate(self.model_filtered)
                     if item["slug"] == self.model), 0
                )
                self.model_index = max(
                    0, min(len(self.model_filtered)-1,
                           self.model_index + self.model_pending_steps)
                )
                self.model_pending_steps = 0
            except Exception as error:
                self.model_error = str(error)
            finally:
                self.model_loading = False
                self.app.invalidate()
        threading.Thread(target=fetch, daemon=True).start()

    def close_models(self) -> None:
        self.model_visible = False
        self.app.layout.focus(self.input)
        self.app.invalidate()

    def choose_model(self) -> None:
        if self.model_loading or not self.model_filtered:
            return
        slug = self.model_filtered[self.model_index]["slug"]
        self.close_models()
        self.change_model(slug)

    def change_model(self, slug: str) -> None:
        if self.select_model is None:
            self.status = "Model selection unavailable"
            self.app.invalidate()
            return
        self.status = "Switching ChatGPT model…"
        self.app.invalidate()
        def worker() -> None:
            try:
                result = self.select_model(slug)
                self.model = result["selected"]
                self.status = f"Model: {self.model}"
            except Exception as error:
                self.status = f"Model selection failed: {error}"
            self.app.invalidate()
        threading.Thread(target=worker, daemon=True).start()

    def display_usage(self) -> None:
        if self.get_usage is None:
            self.status = "Usage information unavailable"
            self.app.invalidate()
            return
        self.status = "Checking ChatGPT Web usage…"
        self.app.invalidate()
        def worker() -> None:
            try:
                usage = self.get_usage()
                rows = [
                    "**ChatGPT Web**",
                    "Remaining messages: **not available from ChatGPT Web**.",
                    "",
                    "**Codex / Work (separate allowance)**",
                ]
                codex = usage.get("codex", {})
                windows = codex.get("windows", {}) if isinstance(codex, dict) else {}
                for key, title in (("primary_window", "Short window"), ("secondary_window", "Long window")):
                    info = windows.get(key)
                    if not isinstance(info, dict):
                        continue
                    remaining = info.get("remaining_percent")
                    reset = info.get("reset_after_seconds")
                    if isinstance(remaining, (float, int)):
                        line = f"{title}: **{remaining:g}% remaining**"
                        if isinstance(reset, int):
                            if reset >= 86400:
                                eta = f"{reset // 86400}d {(reset % 86400)//3600}h"
                            elif reset >= 3600:
                                eta = f"{reset // 3600}h {(reset % 3600)//60}m"
                            else:
                                eta = f"{reset // 60}m"
                            line += f" · resets in {eta}"
                        rows.append(line)
                if not windows:
                    rows.append("Currently unavailable.")
                self.append_system((chr(10) + chr(10)).join(rows))
                self.status = "Usage checked"
            except Exception as error:
                self.status = f"Usage check failed: {error}"
            self.app.invalidate()
        threading.Thread(target=worker, daemon=True).start()

    def render_command_hints(self) -> FormattedText:
        query = self.input.text.strip().casefold()
        matches = [
            (name, description)
            for name, description in SlashCommandCompleter.COMMANDS
            if name.startswith(query)
        ]
        fragments: list[tuple[str, str]] = []
        for name, description in matches[:4]:
            fragments.extend([
                ("class:commands.key", f"  {name:<11}"),
                ("class:commands.description", f"{description}\n"),
            ])
        fragments.append(("class:commands.description", "  Tab  complete    Enter  run"))
        return FormattedText(fragments)

    def make_bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        editor_active = Condition(
            lambda: self.app.layout.current_control is self.input.control
            and not self.model_visible and not self.resume_visible
        )
        completion_visible = Condition(
            lambda: self.app.layout.current_control is self.input.control
            and self.input.buffer.complete_state is not None
        )

        @bindings.add("enter")
        def submit(event: Any) -> None:
            if event.app.layout.current_control is self.input.control:
                state = self.input.buffer.complete_state
                typed = self.input.text.strip()
                names = {name for name, _ in SlashCommandCompleter.COMMANDS}
                if typed.split(" ", 1)[0] in names:
                    self.input.buffer.cancel_completion()
                    self.submit_prompt()
                elif state is not None and state.completions:
                    completion = state.current_completion or state.completions[0]
                    self.input.buffer.apply_completion(completion)
                    if completion.text in names and typed.startswith("/"):
                        self.submit_prompt()
                else:
                    self.submit_prompt()

        @bindings.add("tab")
        def complete_command(event: Any) -> None:
            if event.app.layout.current_control is not self.input.control:
                return
            state = self.input.buffer.complete_state
            if state is not None and state.completions:
                completion = state.current_completion or state.completions[0]
                self.input.buffer.apply_completion(completion)
            else:
                self.input.buffer.start_completion(select_first=True)

        @bindings.add("escape", filter=completion_visible, eager=True)
        def dismiss_completion(_event: Any) -> None:
            self.input.buffer.cancel_completion()

        @bindings.add("escape", "enter")
        def newline(event: Any) -> None:
            if event.app.layout.current_control is self.input.control and not self.busy:
                self.input.buffer.insert_text("\n")

        @bindings.add("c-c")
        def interrupt(event: Any) -> None:
            if self.busy:
                self.status = "Stopping response…"
                connection = self.cancel_connection
                if connection is not None:
                    try:
                        connection.shutdown(2)
                    except OSError:
                        pass
                    try:
                        connection.close()
                    except OSError:
                        pass
                self.app.invalidate()
            elif self.input.text:
                self.input.buffer.reset()
                self.status = "Prompt cleared"
                self.app.invalidate()
            else:
                self.status = "Press /exit or Ctrl+D to quit"
                self.app.invalidate()

        @bindings.add("c-d")
        def exit_app(event: Any) -> None:
            if not self.busy and not self.input.text:
                event.app.exit()

        @bindings.add("c-r", eager=True)
        def resume_picker(_event: Any) -> None:
            self.open_resume()

        @bindings.add("c-v", eager=True)
        def paste_image(_event: Any) -> None:
            self.paste_clipboard_image()

        @bindings.add("up", filter=editor_active, eager=True)
        def prompt_history_up(event: Any) -> None:
            buffer = self.input.buffer
            if buffer.document.cursor_position_row > 0:
                buffer.cursor_up()
                return
            self.move_prompt_history(-1)

        @bindings.add("down", filter=editor_active, eager=True)
        def prompt_history_down(event: Any) -> None:
            buffer = self.input.buffer
            if buffer.document.cursor_position_row < buffer.document.line_count - 1:
                buffer.cursor_down()
                return
            self.move_prompt_history(1)

        @bindings.add("up", filter=self.model_filter, eager=True)
        def model_up(_event: Any) -> None:
            if self.model_loading:
                self.model_pending_steps -= 1
            elif self.model_filtered:
                self.model_index = max(0, self.model_index - 1)
            self.app.invalidate()

        @bindings.add("down", filter=self.model_filter, eager=True)
        def model_down(_event: Any) -> None:
            if self.model_loading:
                self.model_pending_steps += 1
            elif self.model_filtered:
                self.model_index = min(len(self.model_filtered) - 1, self.model_index + 1)
            self.app.invalidate()

        @bindings.add("enter", filter=self.model_filter, eager=True)
        def model_choose(_event: Any) -> None:
            self.choose_model()

        @bindings.add("escape", filter=self.model_filter, eager=True)
        def model_close(_event: Any) -> None:
            self.close_models()

        @bindings.add("up", filter=self.resume_filter, eager=True)
        def resume_up(_event: Any) -> None:
            if self.resume_filtered:
                self.resume_index = max(0, self.resume_index - 1)
                self.resume_list_window.vertical_scroll = max(0, self.resume_index - 2)
                self.app.invalidate()

        @bindings.add("down", filter=self.resume_filter, eager=True)
        def resume_down(_event: Any) -> None:
            if self.resume_filtered:
                self.resume_index = min(
                    len(self.resume_filtered) - 1, self.resume_index + 1
                )
                self.resume_list_window.vertical_scroll = max(0, self.resume_index - 2)
                self.app.invalidate()

        @bindings.add("enter", filter=self.resume_filter, eager=True)
        def resume_selected(_event: Any) -> None:
            self.select_resume()

        @bindings.add("escape", filter=self.resume_filter, eager=True)
        def close_resume(_event: Any) -> None:
            self.close_resume()

        @bindings.add("pageup")
        def page_up(_event: Any) -> None:
            self.pause_following()
            page = self.transcript_page_size()
            self.transcript_window.vertical_scroll = max(
                0, self.transcript_window.vertical_scroll - page
            )

        @bindings.add("pagedown")
        def page_down(_event: Any) -> None:
            self.pause_following()
            self.transcript_window.vertical_scroll += self.transcript_page_size()

        @bindings.add("home", eager=True)
        def prompt_line_start(event: Any) -> None:
            if event.app.layout.current_control is self.input.control:
                self.input.buffer.cursor_position += (
                    self.input.buffer.document.get_start_of_line_position()
                )

        @bindings.add("end", eager=True)
        def prompt_line_end(event: Any) -> None:
            if event.app.layout.current_control is self.input.control:
                self.input.buffer.cursor_position += (
                    self.input.buffer.document.get_end_of_line_position()
                )
                return
            self.follow_output = True
            self.transcript_window.vertical_scroll = 10**9
            self.app.invalidate()

        return bindings

    def pause_following(self) -> None:
        self.follow_output = False

    def transcript_page_size(self) -> int:
        info = self.transcript_window.render_info
        if info is None:
            return 10
        return max(3, info.window_height - 3)

    def load_prompt_history(self) -> list[str]:
        """Recent prompts across saved CLI sessions, newest first."""
        entries: list[str] = []
        for session in local_history.list_recent(100):
            for message in reversed(session.get("messages", [])):
                if message.get("role") != "user":
                    continue
                content = message.get("text")
                if (isinstance(content, str) and content.strip()
                        and (not entries or entries[-1] != content)):
                    entries.append(content)
                if len(entries) >= 200:
                    return entries
        for message in reversed(self.messages):
            text = message.get("text")
            if (message.get("role") == "user" and isinstance(text, str)
                    and text.strip() and (not entries or entries[-1] != text)):
                entries.append(text)
        return entries

    def on_input_changed(self, _buffer: Any) -> None:
        if not getattr(self, "_changing_prompt_history", False):
            self.history_position = -1
            self.history_draft = ""

    def move_prompt_history(self, direction: int) -> None:
        """Up browses older prompts; Down restores the unsent draft."""
        if not self.history_entries:
            return
        if self.history_position == -1:
            if direction > 0:
                return
            self.history_draft = self.input.text
            self.history_position = 0
        else:
            candidate = self.history_position - direction
            if candidate < 0:
                self.history_position = -1
            else:
                self.history_position = min(candidate, len(self.history_entries) - 1)
        content = (self.history_draft if self.history_position == -1
                   else self.history_entries[self.history_position])
        self._changing_prompt_history = True
        try:
            self.input.text = content
            self.input.buffer.cursor_position = len(content)
        finally:
            self._changing_prompt_history = False
        self.app.invalidate()

    def record_prompt_history(self, prompt: str) -> None:
        if not prompt.strip():
            return
        if not self.history_entries or self.history_entries[0] != prompt:
            self.history_entries.insert(0, prompt)
            self.history_entries = self.history_entries[:200]
        self.history_position = -1
        self.history_draft = ""

    def persist_local(self) -> None:
        """Durably record visible CLI messages, including image-only sessions."""
        with self.lock:
            messages = [dict(message) for message in self.messages]
            session_id = self.local_session_id
            server_id = self.conversation_id
            title = self.title
            last_image = self.last_generated_image
        try:
            local_history.save(
                session_id, server_id=server_id, title=title,
                messages=messages, last_image=last_image,
            )
        except (OSError, ValueError) as error:
            self.status = f"Local history could not be saved: {error}"
            if hasattr(self, "app"): self.app.invalidate()

    def append_system(self, text: str) -> None:
        with self.lock:
            self.messages.append({"role": "system", "text": text})
        self.persist_local()
        self.scroll_bottom()

    def scroll_bottom(self, force: bool = True) -> None:
        if force:
            self.follow_output = True
        if self.follow_output:
            self.transcript_window.vertical_scroll = 10**9
        self.app.invalidate()

    def submit_prompt(self) -> None:
        if self.loading_conversation:
            self.status = "Conversation still loading · draft kept"
            self.app.invalidate()
            return
        if self.busy or self.image_busy:
            self.status = "Operation active · draft kept for the next turn"
            self.app.invalidate()
            return
        prompt = self.input.text.strip()
        attachments = list(self.pending_attachments)
        if not prompt and not attachments:
            return
        if not prompt.startswith("/") and self.cooldown_remaining():
            self.status = f"Rate limit · retry in {format_remaining(self.cooldown_remaining())} · draft kept"
            self.app.invalidate()
            return
        self.input.text = ""
        if prompt.startswith("/"):
            self.handle_command(prompt)
            return
        self.record_prompt_history(prompt)
        self.pending_attachments.clear()
        if self.legacy_read_only:
            # The old conversation was outside the CLI Project. Do not make
            # the UI imply its context will be sent to the new project chat.
            self.persist_local()
            self.local_session_id = local_history.new_id()
            self.conversation_id = None
            self.legacy_read_only = False
            self.title = "New conversation"
            with self.lock:
                self.messages = [{
                    "role": "system",
                    "text": "Started a fresh CLI Project chat. The older cached conversation "
                            "is preserved separately, but its contents were not sent to ChatGPT.",
                }]
                self.committed_message_count = 0
                self.history_tail = ""
        prompt = prompt or "Describe the attached image."
        display_prompt = prompt
        if attachments:
            labels = " ".join(f"[📎 {item['name']}]" for item in attachments)
            display_prompt = f"{display_prompt}\n\n{labels}"
        with self.lock:
            self.messages.append({"role": "user", "text": display_prompt})
            self.messages.append({"role": "assistant", "text": ""})
        self.persist_local()
        self.busy = True
        self.active_started = time.monotonic()
        self.last_elapsed = None
        self.status = "Preparing secure request…"
        self.scroll_bottom()
        threading.Thread(
            target=self.run_send, args=(prompt, attachments), daemon=True
        ).start()

    def handle_command(self, command: str) -> None:
        name, _, argument = command.partition(" ")
        if name in ("/exit", "/quit"):
            self.app.exit()
        elif name == "/new":
            self.persist_local()
            self.legacy_read_only = False
            self.local_session_id = local_history.new_id()
            self.last_generated_image = None
            with self.lock:
                self.messages.clear()
                self.committed_message_count = 0
                self.history_tail = ""
            self.conversation_id = None
            if self.set_active_conversation is not None:
                self.set_active_conversation(None)
            self.title = "New conversation"
            self.pending_attachments.clear()
            self.status = "Started a new conversation"
            self.persist_local()
            self.scroll_bottom()
        elif name == "/clear":
            with self.lock:
                self.messages.clear()
                self.committed_message_count = 0
                self.history_tail = ""
            self.status = "Transcript cleared"
            self.persist_local()
            self.scroll_bottom()
        elif name == "/model":
            if argument.strip():
                self.change_model(argument.strip())
            else:
                self.open_models()
        elif name == "/usage":
            self.display_usage()
        elif name == "/resume":
            if argument.strip():
                self.resume_conversation(argument.strip())
            else:
                self.open_resume()
        elif name == "/history":
            if self.conversation_id:
                self.resume_conversation(self.conversation_id)
            else:
                self.status = "No saved conversation yet"
                self.app.invalidate()
        elif name == "/rename":
            self.rename_current_conversation(argument)
        elif name == "/copy":
            self.copy_latest_response()
        elif name in ("/image", "/edit", "/animate"):
            self.start_image_command(name, argument)
        elif name == "/preview":
            self.preview_image_command(argument)
        elif name == "/hd":
            self.hd_image_command(argument, force=True)
        elif name == "/open":
            self.open_image_command(argument)
        elif name == "/remove":
            if self.pending_attachments:
                removed = self.pending_attachments.pop()
                self.status = f"Removed {removed['name']}"
            else:
                self.status = "No pending image to remove"
            self.app.invalidate()
        elif name == "/help":
            self.append_system(
                "**Commands**\n\n"
                "- `/new` start a conversation\n"
                "- `/resume` search recent conversations\n"
                "- `/resume ID` open a conversation directly\n"
                "- `/history` reload this conversation\n"
                "- `/rename TITLE` rename this conversation\n"
                "- `/copy` copy the latest assistant response\n"
                "- `/remove` remove the latest pending image\n"
                "- `/clear` clear the displayed transcript\n"
                "- `/exit` or `/quit` quit"
            )
        else:
            self.status = f"Unknown command: {name}"
            self.app.invalidate()

    def hd_image_command(self, argument: str, *, force: bool = False) -> None:
        """Suspend prompt-toolkit while writing native pixels to the terminal."""
        path = argument.strip().strip('"').strip("'") or self.last_generated_image
        if not path:
            self.status = "Usage: /hd PATH (or generate an image first)"
            self.app.invalidate()
            return
        try:
            from terminal_native import load_png
            load_png(path, limit=64)  # Validate before leaving the application.
        except (OSError, ValueError, ImportError) as error:
            self.status = f"Cannot render image: {error}"
            self.app.invalidate()
            return
        if not (self.native_image_preview or force):
            self.status = "Native images unavailable here; use /preview or /open"
            self.app.invalidate()
            return
        if self.event_loop is None:
            self.status = "Native preview requires a running terminal session"
            self.app.invalidate()
            return
        self.last_generated_image = str(Path(path).expanduser())
        terminal_size = shutil.get_terminal_size((100, 35))
        columns = max(8, min(130, terminal_size.columns - 6))
        rows = max(4, min(34, terminal_size.lines - 8))

        def draw() -> None:
            try:
                sys.stdout.write(chr(10) + "  High-definition image (original: " +
                                 Path(path).name + ")" + chr(10))
                sys.stdout.flush()
                render_native(path, sys.stdout.buffer, columns, rows,
                              tmux_passthrough=bool(os.environ.get("TMUX")))
                sys.stdout.write("  /open to inspect original at full resolution" + chr(10))
                sys.stdout.flush()
                self.status = "HD image sent to the terminal"
            except (ValueError, OSError) as error:
                sys.stdout.write("  HD preview failed: " + str(error) + chr(10))
                sys.stdout.flush()
                self.status = f"HD preview failed: {error}"

        self.event_loop.call_soon_threadsafe(
            lambda: run_in_terminal(draw, render_cli_done=False)
        )
        self.status = "Sending HD image to terminal…"
        self.app.invalidate()

    def open_image_command(self, argument: str) -> None:
        """Open local full-resolution file in the desktop image viewer."""
        path = argument.strip().strip('"').strip("'") or self.last_generated_image
        if not path:
            self.status = "Usage: /open PATH (or generate an image first)"
            self.app.invalidate()
            return
        try:
            from terminal_native import load_png
            load_png(path, limit=64)
            opener = shutil.which("xdg-open") or shutil.which("open")
            if opener is None:
                raise RuntimeError("No desktop image opener found")
            subprocess.Popen([opener, str(Path(path).expanduser().resolve())],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.status = "Opening full-resolution original"
        except (OSError, ValueError, RuntimeError) as error:
            self.status = f"Could not open image: {error}"
        self.app.invalidate()

    def preview_image_command(self, argument: str) -> None:
        path = argument.strip().strip('"').strip("'") or self.last_generated_image
        if not path:
            self.status = "Usage: /preview PATH (or generate an image first)"
            self.app.invalidate()
            return
        try:
            # Validate the preview before inserting it in the transcript.
            render_preview(path, max_columns=8, max_rows=3)
        except (OSError, ValueError, ImportError) as error:
            self.status = f"Cannot preview image: {error}"
            self.app.invalidate()
            return
        self.last_generated_image = str(Path(path).expanduser())
        with self.lock:
            self.messages.append({"role": "image", "text": self.last_generated_image})
        self.persist_local()
        self.status = "Image preview shown inline"
        self.scroll_bottom()

    def start_image_command(self, name: str, argument: str) -> None:
        """Run image-use off the UI thread; no shell or browser credentials shared."""
        if self.image_busy or self.busy:
            self.status = "Another operation is still running"
            self.app.invalidate()
            return
        action = {"/image": "generate", "/edit": "edit", "/animate": "animate"}[name]
        source: str | None = None
        prompt = argument.strip()
        if action == "edit":
            if "|" not in argument:
                self.status = "Usage: /edit PATH | instructions"
                self.app.invalidate()
                return
            reference, _, prompt = argument.partition("|")
            source = reference.strip().strip('"').strip("'") or self.last_generated_image
            prompt = prompt.strip()
            if not source:
                self.status = "No image yet: specify PATH or generate an image first"
                self.app.invalidate()
                return
        if not prompt:
            self.status = f"Usage: {name} PROMPT" if action != "edit" else "Usage: /edit PATH | PROMPT"
            self.app.invalidate()
            return
        with self.lock:
            self.messages.append({
                "role": "user",
                "text": f"{name} {argument.strip()}",
            })
        self.persist_local()
        self.scroll_bottom()
        self.image_busy = True
        self.image_started = time.monotonic()
        self.status = {"generate": "Generating image", "edit": "Editing image", "animate": "Rendering animation"}[action]
        self.app.invalidate()

        def run() -> None:
            try:
                backend = os.environ.get("CHATGPT_WEB_IMAGE_BACKEND", "auto")
                result = generate_image(action, prompt, reference=source, backend=backend)
                self.last_generated_image = result.output
                # Always keep a real image entry in the conversation.
                # Native graphics drawn outside prompt-toolkit vanish on repaint.
                # This persistent inline preview remains available after redraw.
                with self.lock:
                    self.messages.append({"role": "image", "text": result.output})
                self.persist_local()
                self.append_system(
                    f"**{action.title()} completed** · {result.elapsed_seconds}s\n\n"
                    f"Saved to `{result.output}`\n\n"
                    f"**Prompt:** {prompt}\n\n"
                    "Preview displayed above; the original full-resolution file is saved locally. "
                    "Image generation runs separately from the active web conversation."
                )
                self.status = "Image saved"
            except Exception as error:
                self.append_system(f"**Image {action} failed:** {error}")
                self.status = "Image request failed"
            finally:
                self.image_started = None
                self.image_busy = False
                self.app.invalidate()

        threading.Thread(target=run, daemon=True).start()

    def rename_current_conversation(self, title: str) -> None:
        title = title.strip()
        if self.conversation_id is None:
            self.status = "Save the conversation before renaming it"
            self.app.invalidate()
            return
        if not title:
            self.status = "Usage: /rename TITLE"
            self.app.invalidate()
            return
        if self.rename_conversation is None:
            self.status = "Conversation rename is unavailable"
            self.app.invalidate()
            return
        conversation_id = self.conversation_id
        self.status = "Renaming conversation…"
        self.app.invalidate()

        def rename() -> None:
            try:
                self.rename_conversation(conversation_id, title)
                self.title = title
                self.status = "Conversation renamed"
            except Exception as error:
                self.status = f"Rename failed: {error}"
            self.app.invalidate()

        threading.Thread(target=rename, daemon=True).start()

    @staticmethod
    def read_clipboard_png() -> bytes:
        commands = []
        if shutil.which("wl-paste"):
            commands.append(["wl-paste", "--no-newline", "--type", "image/png"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
        if shutil.which("pngpaste"):
            commands.append(["pngpaste", "-"])
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                return result.stdout
        raise RuntimeError("clipboard does not contain a PNG image")

    def paste_clipboard_image(self) -> None:
        if self.uploading_image:
            self.status = "An image upload is already in progress"
            self.app.invalidate()
            return
        if self.upload_image is None:
            self.status = "Image upload is unavailable"
            self.app.invalidate()
            return
        self.uploading_image = True
        self.upload_started = time.monotonic()
        self.status = "Reading image from clipboard…"
        self.app.invalidate()

        def upload() -> None:
            try:
                data = self.read_clipboard_png()
                if len(data) < 24:
                    raise RuntimeError("clipboard PNG is truncated")
                width = int.from_bytes(data[16:20], "big")
                height = int.from_bytes(data[20:24], "big")
                if width < 1 or height < 1:
                    raise RuntimeError("clipboard PNG has invalid dimensions")
                name = datetime.now().strftime("clipboard-%Y%m%d-%H%M%S.png")
                self.status = f"Uploading {width}×{height} image…"
                self.app.invalidate()
                attachment = self.upload_image(
                    data, name, "image/png", width, height
                )
                if not isinstance(attachment, dict) or not attachment.get("id"):
                    raise RuntimeError("image upload returned no file ID")
                self.pending_attachments.append(attachment)
                self.status = "Ready"
            except Exception as error:
                self.status = f"Image paste failed: {error}"
            finally:
                self.uploading_image = False
                self.upload_started = None
                self.app.invalidate()

        threading.Thread(target=upload, daemon=True).start()

    def copy_latest_response(self) -> None:
        with self.lock:
            response = next(
                (
                    message["text"]
                    for message in reversed(self.messages)
                    if message["role"] == "assistant" and message["text"]
                ),
                "",
            )
        if not response:
            self.status = "No assistant response to copy"
            self.app.invalidate()
            return

        response = render_chatgpt_annotations(response)
        clipboard_commands = (
            ("wl-copy", ["wl-copy"]),
            ("xclip", ["xclip", "-selection", "clipboard"]),
            ("xsel", ["xsel", "--clipboard", "--input"]),
            ("pbcopy", ["pbcopy"]),
        )
        for executable, command in clipboard_commands:
            if shutil.which(executable) is None:
                continue
            try:
                subprocess.run(
                    command,
                    input=response,
                    text=True,
                    check=True,
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.status = "Copied latest response"
                self.app.invalidate()
                return
            except (OSError, subprocess.SubprocessError):
                continue

        # OSC 52 is supported by many terminal emulators and remote sessions.
        encoded = base64.b64encode(response.encode()).decode("ascii")
        self.app.output.write_raw(f"\x1b]52;c;{encoded}\x07")
        self.app.output.flush()
        self.status = "Sent latest response to the terminal clipboard"
        self.app.invalidate()

    def open_resume(self) -> None:
        if self.busy or self.loading_conversation or self.resume_visible:
            return
        self.resume_visible = True
        self.resume_error = None
        self.resume_loading = True
        self.resume_items = []
        self.resume_filtered = []
        self.resume_index = 0
        self.resume_search.text = ""
        self.status = "Loading recent conversations…"
        self.app.layout.focus(self.resume_search)
        self.app.invalidate()

        def load_items() -> None:
            try:
                items = self.list_conversations()
                self.resume_items = items
                self.resume_error = None
                self.resume_loading = False
                self.filter_resume()
                self.status = f"{len(items)} recent conversations"
            except Exception as error:
                self.resume_loading = False
                self.resume_error = str(error)
                self.status = f"Conversation list failed: {error}"
            self.app.invalidate()

        threading.Thread(target=load_items, daemon=True).start()

    def filter_resume(self) -> None:
        query = self.resume_search.text.strip().casefold()
        self.resume_filtered = [
            item for item in self.resume_items
            if query in str(item.get("title") or "Untitled").casefold()
            or query in str(item.get("id") or "").casefold()
        ]
        self.resume_index = min(
            self.resume_index, max(0, len(self.resume_filtered) - 1)
        )
        self.resume_list_window.vertical_scroll = max(0, self.resume_index - 2)
        if hasattr(self, "app"):
            self.app.invalidate()

    def close_resume(self) -> None:
        self.resume_visible = False
        self.status = "Ready"
        self.app.layout.focus(self.input)
        self.app.invalidate()

    def select_resume(self) -> None:
        if not self.resume_filtered:
            return
        item = self.resume_filtered[self.resume_index]
        conversation_id = item.get("id")
        if not isinstance(conversation_id, str) or not conversation_id:
            return
        self.close_resume()
        self.resume_conversation(conversation_id)

    def resume_conversation(self, conversation_id: str) -> None:
        self.loading_conversation = True
        self.loading_started = time.monotonic()
        self.status = "Loading conversation…"
        self.app.invalidate()

        def load_selected() -> None:
            try:
                conversation = self.load_conversation(conversation_id)
                messages = []
                for message in conversation.get("messages", []):
                    if (isinstance(message, dict) and
                        message.get("role") in ("user", "assistant", "system", "image")
                        and isinstance(message.get("text"), str)):
                        restored = {"role": message["role"], "text": message["text"]}
                        until = message.get("rate_limit_until")
                        if isinstance(until, (int, float)):
                            restored["rate_limit_until"] = until
                        messages.append(restored)
                    else:
                        visible = visible_chat_message(message)
                        if visible is not None:
                            messages.append(visible)
                media_files = conversation.get("media_files")
                if isinstance(media_files, list):
                    existing_images = {m["text"] for m in messages if m.get("role") == "image"}
                    for image in media_files:
                        if (isinstance(image, str) and image not in existing_images
                                and Path(image).expanduser().is_file()):
                            messages.append({"role": "image", "text": image})
                            existing_images.add(image)
                warning = conversation.get("media_warning")
                if isinstance(warning, str) and warning:
                    messages.append({
                        "role": "system",
                        "text": "An image attachment could not be downloaded: " + warning,
                    })
                if conversation.get("offline"):
                    messages.append({
                        "role": "system",
                        "text": "Loaded a locally cached copy because ChatGPT is temporarily unavailable. "
                                "New messages still require a valid CLI Project connection.",
                    })
                with self.lock:
                    self.messages = messages
                self.rate_limit_until = max((
                    m.get("rate_limit_until", 0) for m in messages
                    if isinstance(m.get("rate_limit_until"), (int, float))
                    and remaining_seconds(m["rate_limit_until"]) > 0
                ), default=None)
                real_id = conversation.get("server_id", conversation.get("id", conversation_id))
                if isinstance(real_id, str) and real_id.startswith(("local:", "legacy:")):
                    real_id = None
                self.conversation_id = real_id
                selected_local_id = conversation.get("local_session_id")
                self.local_session_id = (
                    selected_local_id if isinstance(selected_local_id, str)
                    else local_history.new_id()
                )
                self.last_generated_image = conversation.get("last_image") or next(
                    (m["text"] for m in reversed(messages) if m["role"] == "image"), None
                )
                if self.set_active_conversation is not None:
                    self.set_active_conversation(real_id)
                self.title = conversation.get("title") or "Untitled"
                self.history_entries = self.load_prompt_history()
                self.history_position = -1
                self.legacy_read_only = bool(conversation.get("legacy"))
                if self.legacy_read_only:
                    self.status = "Older cached chat is read-only; your next message creates a CLI Project chat"
                self.persist_local()
                self.commit_loaded_history(messages)
            except Exception as error:
                self.loading_conversation = False
                self.loading_started = None
                self.status = f"Load failed: {error}"
                self.app.invalidate()

        threading.Thread(target=load_selected, daemon=True).start()

    def recover_rate_limited_draft(self, prompt: str) -> None:
        def restore() -> None:
            if not self.input.text.strip():
                self.input.text = prompt
                self.input.buffer.cursor_position = len(prompt)
            self.app.invalidate()
        if self.event_loop is not None:
            self.event_loop.call_soon_threadsafe(restore)
        else:
            restore()

    def run_send(self, prompt: str, attachments: list[dict[str, Any]]) -> None:
        rendered = ""

        def register_connection(connection: Any) -> None:
            self.cancel_connection = connection

        def on_event(event: dict[str, Any]) -> None:
            nonlocal rendered
            event_type = event.get("type")
            text = event.get("text")
            if event_type == "delta" and isinstance(text, str):
                rendered += text
            elif event_type == "replace" and isinstance(text, str):
                rendered = text
            else:
                return
            with self.lock:
                self.messages[-1]["text"] = rendered
            self.status = "Streaming response…"
            self.scroll_bottom(force=False)

        try:
            result = self.send_prompt(
                self.conversation_id,
                prompt,
                attachments,
                on_event,
                register_connection,
            )
            self.conversation_id = result.get("conversation_id", self.conversation_id)
            if self.set_active_conversation is not None:
                self.set_active_conversation(self.conversation_id)
            conversation = result.get("conversation")
            if isinstance(conversation, dict):
                self.title = conversation.get("title") or self.title
                response_model = conversation.get("default_model_slug")
                if isinstance(response_model, str) and response_model:
                    self.model = response_model
                previous_ids = set(result.get("previous_ids", []))
                final_text = ""
                for message in conversation.get("messages", []):
                    if not isinstance(message, dict) or message.get("id") in previous_ids:
                        continue
                    author = message.get("author") or {}
                    if author.get("role") != "assistant":
                        continue
                    content = message.get("content") or {}
                    parts = content.get("parts") or []
                    text_parts = [part for part in parts if isinstance(part, str)]
                    if text_parts:
                        final_text = "\n".join(text_parts)
                if final_text and final_text != rendered:
                    with self.lock:
                        self.messages[-1]["text"] = final_text
            media_files = result.get("media_files")
            if isinstance(media_files, list):
                with self.lock:
                    already = {m["text"] for m in self.messages if m["role"] == "image"}
                    inserted = 0
                    for file in media_files:
                        if (isinstance(file, str) and file not in already
                                and Path(file).expanduser().is_file()):
                            self.messages.append({"role": "image", "text": file})
                            already.add(file)
                            self.last_generated_image = file
                            inserted += 1
                    if inserted and len(self.messages) >= inserted + 1:
                        previous = self.messages[-inserted-1]
                        if previous["role"] == "assistant" and not previous["text"]:
                            previous["text"] = "Image generated and saved locally."
            warning = result.get("media_warning")
            if isinstance(warning, str) and warning:
                self.append_system("Image attachment could not be saved: " + warning)
            self.status = "Ready"
        except Exception as error:
            self.pending_attachments[0:0] = attachments
            retry = retry_after_seconds(str(error))
            if retry is not None:
                deadline = time.time() + retry
                self.rate_limit_until = max(self.rate_limit_until or 0, deadline)
                with self.lock:
                    # An error after a partial stream should also be visible.
                    self.messages[-1]["text"] = f"*Request stopped: {error}*"
                    self.messages[-1]["rate_limit_until"] = deadline
                self.status = f"Rate limit · retry in {format_remaining(retry)}"
                self.recover_rate_limited_draft(prompt)
            else:
                if not rendered:
                    with self.lock:
                        self.messages[-1]["text"] = f"*Request stopped: {error}*"
                self.status = "Response stopped"
        finally:
            if self.active_started is not None:
                self.last_elapsed = time.monotonic() - self.active_started
            self.active_started = None
            self.cancel_connection = None
            # Complete durable state before announcing the worker is idle.
            self.persist_local()
            self.commit_completed_turns()
            self.busy = False
            self.app.invalidate()

    @staticmethod
    def set_terminal_background() -> None:
        """OSC 11 changes background for this terminal session (incl. padding)."""
        if not sys.stdout.isatty() or os.environ.get("CHATGPT_WEB_KEEP_TERMINAL_BG") == "1":
            return
        sys.stdout.write("]11;#151413")
        sys.stdout.flush()

    @staticmethod
    def restore_terminal_background() -> None:
        if not sys.stdout.isatty() or os.environ.get("CHATGPT_WEB_KEEP_TERMINAL_BG") == "1":
            return
        sys.stdout.write("]111")
        sys.stdout.flush()

    def run(self) -> None:
        self.set_terminal_background()
        self.persist_local()
        self.reset_terminal_session()
        self.commit_initial_history()

        def started() -> None:
            self.event_loop = asyncio.get_running_loop()
            self.app.create_background_task(self.animate_activity())
            if self.load_on_start and self.conversation_id is not None:
                self.resume_conversation(self.conversation_id)

        try:
            self.app.run(pre_run=started)
        finally:
            self.persist_local()
            self.restore_terminal_background()


def run_tui(
    list_conversations: ListConversations,
    load_conversation: LoadConversation,
    send_prompt: SendPrompt,
    conversation_id: str | None = None,
    title: str = "New conversation",
    initial_messages: list[dict[str, Any]] | None = None,
    model: str | None = None,
    upload_image: UploadImage | None = None,
    rename_conversation: RenameConversation | None = None,
    set_active_conversation: SetActiveConversation | None = None,
    list_models: Callable[[], dict[str, Any]] | None = None,
    select_model: Callable[[str], dict[str, Any]] | None = None,
    get_usage: Callable[[], dict[str, Any]] | None = None,
    local_session_id: str | None = None,
    last_image: str | None = None,
) -> None:
    ChatTui(
        list_conversations,
        load_conversation,
        send_prompt,
        conversation_id,
        title,
        initial_messages,
        model,
        upload_image,
        rename_conversation,
        set_active_conversation,
        list_models,
        select_model,
        get_usage,
        local_session_id,
        last_image,
    ).run()
