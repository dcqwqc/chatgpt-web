#!/usr/bin/env python3
"""BB ACP v1 adapter for the authenticated, user-local ChatGPT Web CLI.

Each ACP session maps to one CLI Project conversation and has its own model
selection. Execution uses the ChatGPT account's connected tools; this adapter
does not implement a separate ACP shell or translate prose into tool calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import uuid
from typing import Any

from cooldown import retry_after_seconds
from web_content import render_web_elements, embedded_image_assets
# Enabled only after BB's native directive component was installed and checked.
RICH_UI_READY = Path.home() / ".local/state/chatgpt-web/bb-native-rich-ui-ready"

from maintenance.verified_recovery import verified_final
from maintenance.source_activity import observe_source
from maintenance.bb_retry_guard import original_prompt_for_retry

ROOT = Path.home() / ".local/state/chatgpt-web/bb-acp"
SOCKET = Path(os.environ.get("CHATGPT_WEB_ACP_SOCKET",
    str(Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/chatgpt-web-{os.getuid()}")) /
        "chatgpt-web/daemon-v4.sock")))
CLI = os.environ.get("CHATGPT_WEB_ACP_CLI", str(Path.home() / ".local/bin/chatgpt-web"))
SESSION_RE = re.compile(r"^[a-f0-9]{32}$")
MODEL_RE = re.compile(r"^[a-zA-Z0-9._/-]+$")

# Generated media is already persisted by chatgpt-web's normal image workflow.
# Only serve verified local images; never expose arbitrary paths or image tokens.
MEDIA_ROOT = Path.home() / ".local/share/chatgpt-web/images/web-generated"
CONVERSATION_ROOT = Path.home() / ".local/share/chatgpt-web/conversations"
MEDIA_RE = re.compile(r"^file_[A-Za-z0-9]{12,80}\.png$")
CONVERSATION_RE = re.compile(r"^[a-f0-9-]{36}$")
BB_IMAGE_ENDPOINT = "/api/v1/plugins/image-generation/http/cli-image"
BB_WORKER_ENDPOINT = "/api/v1/plugins/image-generation/http/worker-image"
IMAGE_BRIDGE = "/home/qwqc/Projects/bb-plugin-image-generation/bridge.py"

def explicit_image_intent(prompt: str) -> bool:
    normalized = prompt.strip().lower()
    if re.match(r"^(please\s+)?(generate|create|draw|render|illustrate|paint|produce)\b", normalized):
        return bool(re.search(r"\b(image|picture|photo|illustration|artwork|tree|portrait|landscape|logo|sprite|texture)\b", normalized))
    return bool(re.search(r"\b(generate|create|draw|render) (?:me |us |an? )?(?:new )?(?:image|picture|photo)\b", normalized))

def image_edit_intent(prompt: str) -> bool:
    return bool(re.match(r"^\s*(please\s+)?(edit|change|modify|refine|make it|make the image|turn it|remove|add|replace)\b",prompt,re.I))

async def image_bridge_call(action: str, **data: Any) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, IMAGE_BRIDGE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(
        process.communicate(json.dumps({"action": action, **data}).encode("utf8")),timeout=22)
    if process.returncode:
        raise RuntimeError(f"Image bridge {action} failed: " +
                           (err.decode("utf8","replace")[:240] or out.decode("utf8","replace")[:240]))
    result=json.loads(out)
    if not isinstance(result,dict):
        raise ValueError("Image bridge returned an invalid result")
    return result


def generated_media(conversation_id: str | None) -> list[str]:
    if not isinstance(conversation_id, str) or not CONVERSATION_RE.fullmatch(conversation_id):
        return []
    try:
        record = json.loads((CONVERSATION_ROOT / (conversation_id + ".json")).read_text(encoding="utf8"))
    except (OSError, UnicodeError, ValueError):
        return []
    media = record.get("media_files") if isinstance(record, dict) else None
    if not isinstance(media, list):
        return []
    found = []
    for item in media[:16]:
        if not isinstance(item, str):
            continue
        path = Path(item)
        if not MEDIA_RE.fullmatch(path.name):
            continue
        try:
            resolved = path.resolve(strict=True)
            if resolved.parent != MEDIA_ROOT.resolve() or not resolved.is_file():
                continue
            if resolved.stat().st_size > 12 * 1024 * 1024:
                continue
            with resolved.open("rb") as inp:
                if inp.read(8) != b"\x89PNG\r\n\x1a\n":
                    continue
        except OSError:
            continue
        name = resolved.stem
        if name not in found:
            found.append(name)
    return found


def markdown_images(ids: list[str]) -> str:
    return "".join(
        f"\n\n![Generated image]({BB_IMAGE_ENDPOINT}?asset={image_id})\n"
        for image_id in ids
    )


def daemon_request(command: str) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(20.0)
        sock.connect(str(SOCKET))
        sock.sendall(json.dumps({"command": command}).encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        buffer = b""
        while True:
            block = sock.recv(65536)
            if not block:
                break
            buffer += block
            if len(buffer) > 4_000_000:
                raise RuntimeError("Model catalog exceeds maximum size")
        if not buffer:
            raise RuntimeError("ChatGPT Web daemon closed before responding")
    reply = json.loads(buffer)
    if not reply.get("ok"):
        raise RuntimeError(str(reply.get("error", "ChatGPT Web daemon error")))
    result = reply.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Malformed ChatGPT Web daemon response")
    return result


def model_capabilities() -> list[dict[str, Any]]:
    try:
        raw = daemon_request("model_capabilities").get("models", [])
    except (FileNotFoundError, ConnectionRefusedError):
        # The CLI normally starts its own daemon; BB can recover from a reboot.
        import subprocess
        subprocess.run([CLI, "status"], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        raw = daemon_request("model_capabilities").get("models", [])
    if not isinstance(raw, list):
        raise RuntimeError("ChatGPT Web model capabilities not available")
    return [row for row in raw if isinstance(row, dict)]


# Family IDs are the account's default model slugs. Variants are resolved
# from each fresh account catalog, never inferred as available without proof.
FAMILY_ORDER = ("gpt-6", "gpt-6-mini", "gpt-5-6", "gpt-5-6-mini")
FAMILY_LABELS = {
    "gpt-6": "GPT-6", "gpt-6-mini": "GPT-6 Mini",
    "gpt-5-6": "GPT-5.6 Sol", "gpt-5-6-mini": "GPT-5.6 Luna",
}
VARIANT_SUFFIXES = {
    "gpt-6": {"auto": "gpt-6", "instant": "gpt-6-instant",
              "thinking": "gpt-6-thinking"},
    "gpt-6-mini": {"instant": "gpt-6-mini", "thinking": "gpt-6-t-mini"},
    "gpt-5-6": {"auto": "gpt-5-6", "instant": "gpt-5-6-instant",
                "thinking": "gpt-5-6-thinking"},
    "gpt-5-6-mini": {"instant": "gpt-5-6-mini", "thinking": "gpt-5-6-t-mini"},
}
MODE_LABELS = {"auto": "Auto", "instant": "Instant",
               "thinking": "Thinking", "extended": "Extended"}


def build_families(raw: list[dict[str, Any]]):
    available = {item.get("slug"): item for item in raw
                 if isinstance(item, dict) and isinstance(item.get("slug"), str)
                 and not item["slug"].startswith("chatgpt_alpha_")}
    families = {}
    for family in FAMILY_ORDER:
        modes = {
            mode: slug for mode, slug in VARIANT_SUFFIXES[family].items()
            if slug in available
        }
        if "thinking" in modes:
            capabilities = available[modes["thinking"]]
            if any(
                isinstance(x, dict) and x.get("thinking_effort") == "extended"
                for x in capabilities.get("efforts", [])
            ):
                modes["extended"] = modes["thinking"]
        if modes:
            families[family] = modes
    return families


def model_rows(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    families = build_families(raw)
    if not families:
        raise RuntimeError("No supported ChatGPT Web models in this account")
    return [{"modelId": family, "name": FAMILY_LABELS[family]}
            for family in families]


def models() -> list[dict[str, str]]:
    return model_rows(model_capabilities())


def session_path(sid: str) -> Path:
    if not SESSION_RE.fullmatch(sid):
        raise ValueError("Invalid session ID")
    return ROOT / (sid + ".json")


def save_session(s: dict[str, Any]) -> None:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    dest = session_path(s["sessionId"])
    fd, name = tempfile.mkstemp(prefix=".session-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            json.dump(s, f, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, dest)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_session(sid: str) -> dict[str, Any]:
    with session_path(sid).open(encoding="utf8") as f:
        s = json.load(f)
    if not isinstance(s, dict) or s.get("sessionId") != sid:
        raise ValueError("Invalid stored session")
    return s


def accepted_session_for_cwd(cwd: Any) -> dict[str, Any] | None:
    """Recover across a new ACP session ID only for the exact BB workspace.

    BB may allocate a replacement ACP session on a failed-turn retry.
    An explicitly superseded, stalled source remains bound to its original
    conversation, so a user-authorized fresh continuation is never misrouted
    into a new chat.
    """
    if not isinstance(cwd, str) or not cwd or not ROOT.is_dir():
        return None
    matches = []
    resumable = []
    for candidate in ROOT.glob("*.json"):
        if not SESSION_RE.fullmatch(candidate.stem):
            continue
        try:
            session = load_session(candidate.stem)
        except (OSError, ValueError):
            continue
        pending = session.get("pending_turn")
        if session.get("cwd") != cwd:
            continue
        if isinstance(pending, dict) and pending.get("accepted") is True:
            matches.append(session)
        elif (pending is None
              and session.get("archived_accepted_turn", {}).get("resolution")
              == "explicit-user-continuation"
              and isinstance(session.get("conversation_id"), str)
              and CONVERSATION_RE.fullmatch(session["conversation_id"])):
            resumable.append(session)
    if len(matches) > 1:
        raise RuntimeError(
            "Multiple accepted ChatGPT turns exist for this workspace; "
            "ambiguous recovery is blocked without resubmitting.")
    if matches:
        return matches[0]
    if len(resumable) > 1:
        # The original session is authoritative only if all copies refer to
        # exactly one conversation; otherwise a fresh turn must fail closed.
        ids = {item["conversation_id"] for item in resumable}
        if len(ids) != 1:
            raise RuntimeError("Conflicting saved ChatGPT conversations for workspace")
        resumable.sort(key=lambda item: item["sessionId"])
    return resumable[-1] if resumable else None


def remove_bb_system_envelope(text: str) -> str:
    """BB passes host/plugin tool instructions inside a tagged text envelope.

    The Web CLI has no native BB ACP tool runtime. Forwarding this
    enormous agent-only envelope as the user's message is misleading, can
    exceed browser composer limits, and can cause send failures. Preserve
    every byte after a properly delimited envelope (the actual user input).
    Never remove arbitrary user text appearing elsewhere in the prompt.
    """
    raw = text.lstrip()
    prefix = "<system_instructions>"
    suffix = "</system_instructions>"
    if not raw.startswith(prefix):
        return text
    end = raw.find(suffix, len(prefix))
    if end < 0:
        return text
    return raw[end + len(suffix):].lstrip()


def load_bb_core_policy() -> str:
    """Read the explicitly published BB-wide contract, not the mutable draft.

    The Web CLI backend cannot consume native BB ACP tool/system envelopes,
    so the published core policy is sent as text on the first turn of each
    newly created Web conversation only.
    """
    path = Path.home() / ".bb" / "AGENTS.md"
    try:
        if not path.is_file():
            raise RuntimeError("BB universal Core Instructions are not published")
        if path.stat().st_size > 256_000:
            raise RuntimeError("BB Core Instructions exceed safe adapter limit")
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            raise RuntimeError("BB universal Core Instructions are empty")
        return content
    except OSError as exc:
        raise RuntimeError("Unable to load BB Core Instructions") from exc


def adapter_policy(session: dict[str, Any]) -> tuple[str, str]:
    """Describe the actual execution boundary, including on legacy chats.

    Native ChatGPT connectors and BB ACP tools are different capabilities.
    Do not disable the former just because this adapter lacks the latter.
    Fingerprint the published policy and workspace so a successful next turn
    corrects old advice without creating a replacement conversation.
    """
    context = json.dumps({"host": socket.gethostname(), "cwd": session.get("cwd")},
                         ensure_ascii=False)
    notice = """[BB ChatGPT Web adapter — capability and execution contract v2]
This contract replaces any earlier adapter notice saying this connection is
text-only advisory or has no filesystem, shell or tool access.
You are the user's ChatGPT conversation in BB. Use the tools actually available
in this ChatGPT session, including connected MCP apps and web search. This
adapter does not provide native BB ACP tools; that does not remove the account's
connected tools. Discover their real schemas and try the supported route before
claiming a capability is absent. A listed tool is not proof its service is healthy.
For QWQC MCP, when available, use search_tools/find_mcp_tools with short keywords,
then describe_tool/get_mcp_tool_schema and execute_tool. Discover the Philipedia
terminal bridge for filesystem, Git and shell work on the BB host. Prefer
structured file/Git tools. Inspect the host and applicable AGENTS.md before edits.
If QWQC search returns unrelated smart-routing results or describe_tool rejects
its advertised schema with 'toolName parameter is required', its gateway is
intercepting those reserved names. Use the compatible discovery route:
execute_tool(server="chatgpt", tool="find_mcp_tools", arguments={"query": "..."})
then execute_tool(server="chatgpt", tool="get_mcp_tool_schema",
arguments={"server": "discovered server", "tool": "discovered tool"}).
These routes discover schemas only; invoke the actual allowed server/tool with
execute_tool after reading its schema. If an alias is denied, report that route
as unavailable and use another actually connected tool; do not change permissions.
An offline Mirai connector does not establish that Philipedia is unavailable.
An error in one connector blocks only that route: try another already authorized
connected route, and continue independent work. Never bypass access restrictions,
invent a tool, or claim an action happened without its result.
When the user asks for an action, carry out the authorized work and verify it.
Do not replace execution with a proposed configuration or stop at the first
recoverable error. For a real blocker, state the exact failed route, completed
work, remaining work and smallest required next action. Do not infer missing
credentials from a single process environment; use the credential catalog when
available, keeping secret values out of chat, logs and source files.
BB runtime context (not proof of remote connector reachability):
""" + context + """
Write clear, concise Markdown for the BB transcript. Do not emit ChatGPT-only
XML/JSX UI tags such as <box>, <row>, <Cite>, or internal citation reference IDs.
Use ordinary Markdown links to sources you actually consulted. Keep code samples
as code. Distinguish implemented, tested and deployed outcomes.
[End BB ChatGPT Web adapter contract]
"""
    policy = ("[BB Agent Control — published Core Instructions]\n"
              + load_bb_core_policy().rstrip() + "\n"
              + "[End published Core Instructions]\n\n" + notice)
    return policy, hashlib.sha256(policy.encode("utf-8")).hexdigest()


def readable_tool_activity(name: str, arguments: str) -> tuple[str, str]:
    """Use observed tool identity and arguments, not an invented action label.

    Metadata is presented in BB's native collapsible tool item. The title
    stays compact while the detail retains the provider's actual call text.
    """
    descriptions = {
        "functions.exec": "Run connected tools",
        "api_tool.call_tool": "Use connected app",
        "web.run": "Search and retrieve web data",
        "python.exec": "Run Python",
        "python_user_visible.exec": "Run Python",
        "container.exec": "Run terminal command",
        "image_gen.text2im": "Generate image",
    }
    human_name = descriptions.get(name, name.replace("mcp__", "").replace("__", " · "))
    subject = ""
    # A structured tool envelope can expose the exact target operation.
    # Do not claim a specific operation when the envelope is not parseable.
    try:
        parsed = json.loads(arguments)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        # ChatGPT connected-app calls provide the exact app and method path.
        # The link_* segment is a private routing handle, not a human label.
        path = parsed.get("path")
        if isinstance(path, str) and path.startswith("/"):
            path_parts = [part for part in path.split("/") if part and
                          not part.startswith("link_")]
            if len(path_parts) >= 2:
                human_name = path_parts[0]
                subject = path_parts[-1].replace("_", " ")
        for key in ("tool", "recipient_name", "tool_name", "operation", "command"):
            if subject:
                break
            value = parsed.get(key)
            if isinstance(value, str) and value and len(value) < 100:
                subject = value.replace("mcp__", "").replace("__", " · ")
                break
        if not subject and isinstance(parsed.get("server"), str):
            subject = parsed["server"]
    if not subject and name.startswith("mcp__"):
        human_name = name.removeprefix("mcp__").replace("__", " · ")
    title = human_name + (" · " + subject if subject and subject != name else "")
    return title[:240], arguments


def concise_tool_details(arguments: str) -> str:
    """Show only safe, observed short command metadata in the BB title."""
    try:
        payload = json.loads(arguments)
    except (ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    args = payload.get("args")
    if not isinstance(args, dict):
        return ""
    host = args.get("host")
    host = host if isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", host) else ""
    command = args.get("command")
    if not isinstance(command, str) or not command or len(command) > 90 or "\n" in command:
        return host
    if re.search(r"(?i)(bearer|password|secret|token|api[_-]?key|\s-H\s|--header|://[^/]*@)", command):
        return host
    return (host + " · " if host else "") + command


SENSITIVE_TOOL_KEY = re.compile(
    r"(?i)(?:authorization|password|secret|token|api[_-]?key|cookie|credential|private[_-]?key|session[_-]?key|access[_-]?key)"
)
SENSITIVE_TOOL_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_./~+\-=]{8,}")


def safe_tool_input(detail: str) -> dict[str, Any] | None:
    """Give BB its supported structured ACP rawInput shape, never a string.

    String rawInput was silently discarded by BB's ACP bridge. Keep nested
    objects where available and redact credential-bearing fields recursively.
    """
    if not detail.strip():
        return None
    try:
        value = json.loads(detail)
    except (TypeError, ValueError):
        value = {"input": detail[:4000]}
    if not isinstance(value, dict):
        value = {"input": str(value)[:4000]}

    def clean(item: Any, depth: int = 0) -> Any:
        if depth > 6:
            return "[nested value omitted]"
        if isinstance(item, dict):
            return {str(k)[:120]: ("[redacted]" if SENSITIVE_TOOL_KEY.search(str(k))
                                  else clean(v, depth + 1))
                    for k, v in list(item.items())[:50]}
        if isinstance(item, list):
            return [clean(v, depth + 1) for v in item[:40]]
        if isinstance(item, str):
            return SENSITIVE_TOOL_VALUE.sub("Bearer [redacted]", item[:4000])
        return item if isinstance(item, (int, float, bool)) or item is None else "[unsupported value]"

    return clean(value)


class Server:
    def __init__(self):
        self.sessions: dict[str, dict[str, Any]] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.model_cache: list[dict[str, str]] | None = None
        self.model_capabilities_cache: dict[str, dict[str, Any]] = {}
        self.write_lock = asyncio.Lock()

    async def emit(self, obj: dict[str, Any]):
        async with self.write_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    async def update(self, sid: str, text: str):
        if text:
            text = render_web_elements(text, rich_ui=RICH_UI_READY.is_file(),
                                       image_assets=(frozenset(generated_media(
                                           self.sessions.get(sid, {}).get("conversation_id")))
                                           if "<AsyncImage" in text else frozenset()))
            await self.emit({"jsonrpc": "2.0", "method": "session/update",
                             "params": {"sessionId": sid,
                                        "update": {"sessionUpdate": "agent_message_chunk",
                                                   "content": {"type": "text", "text": text}}}})

    async def activity(self, sid: str, call_id: str, title: str,
                       *, started: bool = False, status: str = "in_progress",
                       kind: str = "other", detail: str = "",
                       tool_name: str = "") -> None:
        """Render a truthful, replaceable ACP activity row in BB."""
        if not title.strip():
            return
        await self.emit({"jsonrpc": "2.0", "method": "session/update",
                         "params": {"sessionId": sid, "update": {
                             "sessionUpdate": "tool_call" if started else "tool_call_update",
                             "toolCallId": call_id, "title": title[:240],
                             "kind": kind, "status": status,
                             **({"name": tool_name[:120]} if tool_name else {}),
                             **({"rawInput": safe_tool_input(detail)}
                                if detail else {})}}})

    def catalog(self):
        details = model_capabilities()
        self.model_capabilities_cache = {
            item["slug"]: item for item in details
            if isinstance(item.get("slug"), str)
        }
        self.variants = build_families(details)
        self.model_cache = model_rows(details)
        return self.model_cache

    def normalize_session(self, session: dict[str, Any]) -> None:
        """Migrate old slug-based sessions without dropping user preferences."""
        if not hasattr(self, "variants"):
            self.catalog()
        current = session.get("model")
        if current not in self.variants:
            for family, modes in self.variants.items():
                matching = next((mode for mode, slug in modes.items()
                                 if slug == current), None)
                if matching:
                    old_depth = session.get("depth")
                    legacy_effort = session.get("reasoning")
                    session["model"] = family
                    session["mode"] = (
                        "extended" if matching == "thinking"
                        and "extended" in modes
                        and (old_depth == "extended" or legacy_effort == "high")
                        else matching
                    )
                    break
        elif session.get("mode") == "thinking" and (
                session.get("depth") == "extended" or
                session.get("reasoning") == "high") and (
                "extended" in self.variants[current]):
            session["mode"] = "extended"
        if session.get("model") not in self.variants:
            session["model"] = next(iter(self.variants))
        modes = self.variants[session["model"]]
        if session.get("mode") not in modes:
            session["mode"] = ("thinking" if "thinking" in modes
                               else "auto" if "auto" in modes
                               else next(iter(modes)))
        # Reasoning is represented by a single native BB control, so there
        # are no unsupported/deceptive extra "depth" options on Mini/Instant.
        session.pop("depth", None)
        session.pop("reasoning", None)

    def pending_recovery_ui(self, session: dict[str, Any]) -> dict[str, Any]:
        """Return truthful, narrowly scoped controls with NO model API lookup.

        An accepted original message already has a fixed model and mode.
        During GET-only reconciliation, a model-catalog outage must never stop
        delivery of that saved answer or offer a misleading new model choice.
        """
        pending = session.get("pending_turn")
        model = session.get("model")
        mode = session.get("mode")
        if (not isinstance(pending, dict) or pending.get("accepted") is not True
                or not isinstance(pending.get("conversation_id"), str)
                or not CONVERSATION_RE.fullmatch(pending["conversation_id"])
                or not isinstance(pending.get("message_id"), str)
                or not pending.get("message_id")
                or model not in FAMILY_LABELS or mode not in MODE_LABELS):
            raise RuntimeError("Cannot restore accepted session without verified recovery state")
        selected = {"modelId": model, "name": FAMILY_LABELS[model]}
        return {
            "models": {"currentModelId": model, "availableModels": [selected]},
            "configOptions": [{
                "id": "model", "name": "Model", "category": "model",
                "type": "select", "currentValue": model,
                "options": [{"value": model, "name": FAMILY_LABELS[model]}],
            }, {
                "id": "mode", "name": "Mode", "category": "thought_level",
                "type": "select", "currentValue": mode,
                "options": [{"value": mode, "name": MODE_LABELS[mode]}],
            }],
        }

    def mode_options(self, family: str) -> list[dict[str, str]]:
        return [{"value": key, "name": MODE_LABELS[key]}
                for key in self.variants[family]]

    def config_options(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        self.normalize_session(session)
        options = [{
            "id": "model", "name": "Model", "category": "model",
            "type": "select", "currentValue": session["model"],
            "options": [{"value": m["modelId"], "name": m["name"]}
                        for m in self.model_cache],
        }, {
            # BB's native reasoning row doubles as the model's operating mode.
            "id": "mode", "name": "Mode", "category": "thought_level",
            "type": "select", "currentValue": session["mode"],
            "options": self.mode_options(session["model"]),
        }]
        return options

    def model_state(self, current: str):
        items = self.model_cache or self.catalog()
        if current not in {row["modelId"] for row in items}:
            raise ValueError("Model family is not available")
        return {"currentModelId": current, "availableModels": items}

    async def replay(self, sid: str, session: dict[str, Any]):
        conversation_id = session.get("conversation_id")
        if not conversation_id:
            return
        process = await asyncio.create_subprocess_exec(
            CLI, "show", conversation_id, "--output", "json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(process.communicate(), timeout=45)
        if process.returncode:
            raise RuntimeError("Could not load ChatGPT Web history: "
                               + err.decode("utf8", "replace")[:400])
        conversation = json.loads(out)
        image_assets = frozenset(generated_media(conversation_id))
        for msg in conversation.get("messages", []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("text")
            if not isinstance(content, str) or not content:
                continue
            if role == "assistant":
                content = render_web_elements(content, rich_ui=RICH_UI_READY.is_file(),
                                              image_assets=image_assets)
            await self.emit({"jsonrpc": "2.0", "method": "session/update",
                             "params": {"sessionId": sid, "update": {
                                 "sessionUpdate": ("user_message_chunk"
                                                   if role == "user" else
                                                   "agent_message_chunk"),
                                 "content": {"type": "text", "text": content}}}})

    async def run_image_fallback(self, sid: str, prompt: str, session: dict[str, Any]) -> None:
        # BB's web-CLI provider has no dynamic tool runtime. Use the already
        # authorized owner image worker if CLI request preparation failed, or
        # continue a prior browser-generated image edit without losing lineage.
        parent=session.get("image_version_id")
        prior_session=session.get("image_session_id")
        editing=bool(prior_session and parent and image_edit_intent(prompt))
        request={"prompt":prompt,"title":"BB ChatGPT Web image"}
        if editing:
            request.update({"session_id":prior_session,"parent_version_id":parent})
        job=await image_bridge_call("generate",**request)
        await self.update(sid,"Generating image through ChatGPT Web…")
        deadline=asyncio.get_running_loop().time()+390
        result={"state":job.get("state"),"job_id":job.get("job_id")}
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(3)
            result=await image_bridge_call("status",job_id=job["job_id"])
            if result.get("state") in ("completed","blocked","failed","timeout","challenge","login_required","cancelled"):
                break
        if result.get("state")=="completed":
            image=result.get("image") or {}
            path=Path(str(image.get("path","")))
            asset=path.name
            if not re.fullmatch(r"[a-f0-9]{32}",asset) or not path.is_file():
                raise RuntimeError("Image worker reported completion without a verified local image")
            session["image_session_id"]=str(image["session_id"])
            session["image_version_id"]=str(image["version_id"])
            save_session(session)
            await self.update(sid, f"\n\n![Generated image]({BB_WORKER_ENDPOINT}?asset={asset})\n")
            return
        state=result.get("state","pending")
        detail=str(result.get("detail") or "")
        await self.update(sid, f"Image generation did not complete ({state}). {detail}. "
                          f"Job: {job['job_id']}. No image has been generated for this request.")

    async def recover_accepted_turn(
        self, request_id: Any, sid: str, session: dict[str, Any],
        pending: dict[str, Any], user_prompt: str,
    ) -> None:
        """Reconcile an accepted turn by GET only; never send a second prompt."""
        conversation_id = pending.get("conversation_id")
        message_id = pending.get("message_id")
        if (not isinstance(conversation_id, str)
                or not CONVERSATION_RE.fullmatch(conversation_id)
                or not isinstance(message_id, str) or not message_id):
            raise RuntimeError(
                "Accepted ChatGPT request has no verified recovery IDs. "
                "Preserved without resubmitting; manual correlation is required.")
        row_id = "chatgpt-recover-" + hashlib.sha256(message_id.encode()).hexdigest()[:12]
        await self.activity(sid, row_id, "ChatGPT Web · Recovering original response",
                            started=True, kind="other")
        deadline = asyncio.get_running_loop().time() + 1050
        attempt = 0
        stale_observations = 0
        last_source_time: float | None = None
        while asyncio.get_running_loop().time() < deadline:
            attempt += 1
            observed_source = False
            delay = 75  # Bound GETs; fast polling can exhaust account rate limits.
            try:
                process = await asyncio.create_subprocess_exec(
                    CLI, "show", conversation_id, "--output", "json",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
                output, error = await asyncio.wait_for(process.communicate(), timeout=38)
                if process.returncode != 0:
                    # A GET throttle is not a prompt failure. Honor the
                    # server's Retry-After before another read-only check.
                    cooldown = retry_after_seconds(error.decode("utf8", "replace"))
                    if cooldown is not None:
                        delay = max(delay, cooldown + 5)
                if process.returncode == 0 and len(output) <= 12_000_000:
                    document = json.loads(output)
                    answer = verified_final(
                        document, conversation_id=conversation_id,
                        message_id=message_id, user_prompt=user_prompt)
                    observation = observe_source(
                        document, conversation_id=conversation_id,
                        message_id=message_id, user_prompt=user_prompt,
                        now=time.time(),
                    )
                    if observation is not None and answer is None:
                        observed_source = True
                        fresh = observation["age_seconds"] < 360
                        label = ("Source still active" if fresh else
                                 "Source inactive · final response not saved")
                        await self.activity(
                            sid, row_id,
                            f"ChatGPT Web · {label} · "
                            f"{observation['tool_calls']} saved tool calls · "
                            f"last {observation['last_at']}",
                            status="in_progress", kind="other",
                            detail=observation["detail"],
                        )
                        if (not fresh and last_source_time == observation["last_activity"]):
                            stale_observations += 1
                        else:
                            stale_observations = 1 if not fresh else 0
                        last_source_time = observation["last_activity"]
                        if stale_observations >= 2:
                            raise RuntimeError(
                                f"Original ChatGPT source inactive since {observation['last_at']}; "
                                "no verified final answer exists. The broken SSE "
                                "cannot be reattached and no prompt was resent. "
                                "Saved ChatGPT conversation and BB history are preserved.")
                    if answer is not None:
                        session["conversation_id"] = conversation_id
                        session["adapter_policy_hash"] = adapter_policy(session)[1]
                        session.pop("pending_turn", None)
                        save_session(session)
                        await self.update(sid, answer)
                        await self.activity(sid, row_id,
                                            "ChatGPT Web · Original response recovered",
                                            status="completed", kind="other")
                        await self.emit({"jsonrpc": "2.0", "id": request_id,
                                         "result": {"stopReason": "end_turn"}})
                        return
            except (OSError, ValueError, asyncio.TimeoutError):
                pass  # Transient read-only fetch failure is not permission to resend.
            if not observed_source:
                await self.activity(
                    sid, row_id,
                    f"ChatGPT Web · Source read unavailable · "
                    f"next read-only check in {delay}s",
                    kind="other",
                )
            await asyncio.sleep(min(delay, max(0, deadline - asyncio.get_running_loop().time())))
        raise RuntimeError(
            "The original accepted ChatGPT turn has not produced a verifiable "
            "final answer. Recovery is still pending; no request was resubmitted.")

    async def do_prompt(self, request_id: Any, sid: str, blocks: list, rate_retries: int = 0):
        session = self.sessions[sid]
        pieces = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                raise ValueError("ChatGPT Web ACP currently supports text prompts only")
            if block.get("text"):
                pieces.append(str(block["text"]))
        if not pieces:
            raise ValueError("Text prompt is empty")
        prompt = remove_bb_system_envelope("\n".join(pieces))
        if not prompt.strip():
            raise ValueError("User prompt is empty after removing BB host instructions")
        user_prompt = prompt
        prompt_hash = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
        pending = session.get("pending_turn")
        if isinstance(pending, dict) and pending.get("accepted") is True:
            original_prompt = user_prompt
            if pending.get("prompt_sha256") != prompt_hash:
                original_prompt = original_prompt_for_retry(
                    session.get("cwd"), pending, user_prompt, Path.home() / ".bb")
                if original_prompt is None:
                    raise RuntimeError(
                        "A previous accepted ChatGPT turn needs read-only recovery "
                        "before a different prompt can be sent. No prompt was submitted.")
            try:
                await self.recover_accepted_turn(request_id, sid, session, pending, original_prompt)
            finally:
                self.running.pop(sid, None)
            return

        previous_image_order = generated_media(session.get("conversation_id"))
        previous_images = set(previous_image_order)
        if session.get("image_session_id") and image_edit_intent(prompt):
            await self.run_image_fallback(sid, prompt, session)
            await self.emit({"jsonrpc":"2.0","id":request_id,
                             "result":{"stopReason":"end_turn"}})
            return
        # An explicit "show the previous image" follow-up is resolved from the
        # existing saved image, avoiding unnecessary duplicate generation.
        if (re.search(r"\b(show|display|open)\b", prompt, re.I)
                and re.search(r"\b(image|picture|photo|tree)\b", prompt, re.I)
                and re.search(r"\b(previous|generated|last|already)\b", prompt, re.I)
                and previous_images):
            await self.update(sid, "Here is the image from this conversation:" +
                              markdown_images([previous_image_order[-1]]))
            await self.emit({"jsonrpc": "2.0", "id": request_id,
                             "result": {"stopReason": "end_turn"}})
            return
        policy, policy_hash = adapter_policy(session)
        if (not session.get("conversation_id")
                or session.get("adapter_policy_hash") != policy_hash):
            prompt = policy + "\n\n" + prompt
        args = [CLI, "send", session["conversation_id"]] if session.get("conversation_id") else [CLI, "new"]
        self.normalize_session(session)
        slug = self.variants[session["model"]][session["mode"]]
        args += ["--model", slug]
        if session["mode"] in ("thinking", "extended"):
            capabilities = self.model_capabilities_cache.get(slug, {})
            effort = "extended" if session["mode"] == "extended" else "standard"
            if any(
                row.get("thinking_effort") == effort
                for row in capabilities.get("efforts", [])
                if isinstance(row, dict)
            ):
                args += ["--thinking-effort", effort]
        args += ["--output", "jsonl", prompt]
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.processes[sid] = process
        rendered = ""
        final = ""
        new_conversation_id = None
        errors = []
        submitted = False
        submission = {"prompt_sha256": prompt_hash, "accepted": False,
                      "message_id": None, "conversation_id": session.get("conversation_id")}
        activity_prefix = "chatgpt-" + uuid.uuid4().hex[:12]
        phase_title = None
        activity_rows: set[str] = set()
        activity_titles: dict[str, str] = {}
        last_preview = ""
        try:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeError):
                    continue
                typ = event.get("type")
                # The CLI marks a 2xx accepted send explicitly. A later 429
                # can be a read/verification throttle; resending it would
                # duplicate a user message, so only retry pre-acceptance 429s.
                if typ == "submission_identity" and isinstance(event.get("message_id"), str):
                    submission["message_id"] = event["message_id"]
                if typ == "stream_conversation_id" and isinstance(event.get("conversation_id"), str):
                    submission["conversation_id"] = event["conversation_id"]
                if event.get("submitted") is True:
                    submitted = True
                    submission["accepted"] = True
                if submission["accepted"] and submission["message_id"]:
                    # Persist observed request identity while the SSE is alive.
                    # This survives a local daemon/ACP crash; no prompt replay.
                    if session.get("pending_turn") != submission:
                        session["pending_turn"] = submission.copy()
                        save_session(session)
                if typ == "phase" and isinstance(event.get("status"), str):
                    title = event["status"].strip()
                    if title and title != phase_title:
                        phase_title = title
                        await self.activity(sid, activity_prefix + "-request",
                                            "ChatGPT Web · " + title,
                                            started="request" not in activity_rows,
                                            kind="think")
                        activity_rows.add("request")
                        activity_titles["request"] = "ChatGPT Web · " + title
                elif typ in ("tool_start", "tool_update") and isinstance(event.get("id"), str):
                    raw_id = event["id"]
                    name = event.get("name")
                    if isinstance(name, str) and (typ == "tool_update" or raw_id not in activity_rows):
                        started = raw_id not in activity_rows
                        activity_rows.add(raw_id)
                        arguments = event.get("arguments", "")
                        detail = arguments.strip() if isinstance(arguments, str) else ""
                        # Tool payloads sometimes include auth headers. Do not
                        # reproduce recognizable credentials in the BB activity.
                        sanitized = re.sub(
                            r'(?i)(authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[^,\s}]+',
                            r'\1=[redacted]', detail)
                        sanitized = re.sub(
                            r'(?i)(["\'](?:authorization|api[_-]?key|password|secret|token)["\']\s*:\s*)'
                            r'(["\'])(?:\\.|(?!\2).)*?\2',
                            r'\1"[redacted]"', sanitized)
                        sanitized = re.sub(
                            r'(?i)\bBearer\s+[A-Za-z0-9_./~+\-=]{8,}',
                            'Bearer [redacted]', sanitized)
                        activity_titles[raw_id], _ = readable_tool_activity(name, sanitized)
                        # BB's generic item uses the ACP name, not the title,
                        # as its expanded card heading. Prefer the observed
                        # connected app rather than the meaningless "other".
                        tool_name = activity_titles[raw_id].split(" · ", 1)[0]
                        native_title = event.get("native_title")
                        if isinstance(native_title, str) and native_title.strip():
                            native_title = native_title.strip()
                            native_title = re.sub(
                                r'(?i)(authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[^,\s}]+',
                                r'\1=[redacted]', native_title)
                            activity_titles[raw_id] = native_title[:180]
                        specifics = concise_tool_details(sanitized)
                        if specifics:
                            activity_titles[raw_id] = (
                                activity_titles[raw_id][:170] + " · " + specifics)[:240]
                        await self.activity(sid, activity_prefix + "-tool-" +
                                            hashlib.sha256(raw_id.encode()).hexdigest()[:12],
                                            activity_titles[raw_id], started=started,
                                            kind="other", detail=sanitized[:4000],
                                            tool_name=tool_name)
                elif typ == "tool_end" and isinstance(event.get("id"), str):
                    raw_id = event["id"]
                    if raw_id in activity_rows:
                        await self.activity(sid, activity_prefix + "-tool-" +
                                            hashlib.sha256(raw_id.encode()).hexdigest()[:12],
                                            activity_titles[raw_id],
                                            status="failed" if event.get("status") == "failed"
                                            else "completed")
                        activity_rows.remove(raw_id)
                elif typ == "commentary" and isinstance(event.get("id"), str):
                    raw_id = "commentary-" + event["id"]
                    body = event.get("text")
                    if isinstance(body, str) and body.strip():
                        # Commentary is public assistant text. A native ACP
                        # message chunk preserves Markdown, spacing and full
                        # paragraphs; a tool title truncates at 240 chars.
                        prior = activity_titles.get(raw_id, "")
                        current = body.strip()
                        if current.startswith(prior):
                            new_text = current[len(prior):]
                            if new_text:
                                await self.update(sid, new_text if prior else "\n\n" + current)
                        elif current != prior:
                            # Provider replaced a snapshot. ACP cannot retract
                            # already appended text, so publish the correction
                            # rather than silently dropping the revision.
                            await self.update(sid, "\n\n" + current + "\n\n")
                        activity_titles[raw_id] = current
                # Browser SSE supplies mutable assistant snapshots. ACP's
                # agent_message_chunk is append-only: publishing a snapshot
                # before the final answer makes any later "replace" event
                # duplicate the response in BB. Reconcile locally, then emit
                # just one canonical answer after the CLI finishes.
                if typ == "delta" and isinstance(event.get("text"), str):
                    rendered += event["text"]
                elif typ == "replace" and isinstance(event.get("text"), str):
                    rendered = event["text"]
                # Show provisional text in a replaceable *activity* row. ACP
                # answer chunks are append-only, so only verified final content
                # is emitted as the actual answer. Throttle updates to avoid
                # swamping BB's event log for each SSE token.
                if typ in ("delta", "replace") and rendered.strip():
                    preview = rendered.strip()[-175:].replace("\n", " ")
                    if ("preview" not in activity_rows or
                            typ == "replace" or len(rendered) - len(last_preview) >= 48):
                        activity_titles["preview"] = "Live answer preview · " + preview
                        await self.activity(sid, activity_prefix + "-preview",
                                            activity_titles["preview"],
                                            started="preview" not in activity_rows,
                                            kind="think")
                        activity_rows.add("preview")
                        last_preview = rendered
                if (typ == "message" and event.get("role") == "assistant"
                      and event.get("channel") in (None, "final")
                      and event.get("recipient") in (None, "all")):
                    if isinstance(event.get("text"), str) and event["text"].strip():
                        final += ("\n" if final else "") + event["text"]
                elif "conversation_id" in event and isinstance(event["conversation_id"], str):
                    new_conversation_id = event["conversation_id"]
            assert process.stderr is not None
            stderr = (await process.stderr.read()).decode("utf8", "replace")[:1500].strip()
            exit_code = await process.wait()
            if exit_code != 0:
                errors.append(stderr or f"CLI exited with code {exit_code}")
            if errors:
                # A user turn already accepted by ChatGPT is NOT a new failure
                # that BB should retry by sending another prompt. The website
                # may still be executing tools and can finish normally after
                # the SSE peer drops. Keep this BB turn alive and reconcile the
                # exact accepted message ID using GET only.
                accepted = session.get("pending_turn")
                if (submission.get("accepted") is True
                        and isinstance(accepted, dict)
                        and accepted.get("accepted") is True
                        and accepted.get("prompt_sha256") == prompt_hash
                        and isinstance(accepted.get("message_id"), str)
                        and accepted.get("message_id")
                        and isinstance(accepted.get("conversation_id"), str)
                        and CONVERSATION_RE.fullmatch(accepted["conversation_id"])):
                    await self.activity(
                        sid, activity_prefix + "-request",
                        "ChatGPT Web · Connection interrupted · recovering saved original",
                        started="request" not in activity_rows, kind="other")
                    await self.recover_accepted_turn(
                        request_id, sid, session, accepted, user_prompt)
                    return
                # This specific error occurs before an API request is prepared.
                # Do not auto-retry any ambiguous send failure: duplicates matter.
                if (not rendered.strip() and not final.strip()
                        and any("Chrome request preparation failed" in msg for msg in errors)
                        and explicit_image_intent(user_prompt)):
                    await self.run_image_fallback(sid, user_prompt, session)
                    await self.emit({"jsonrpc":"2.0","id":request_id,
                                     "result":{"stopReason":"end_turn"}})
                    return
                detail = "; ".join(errors)
                delay = retry_after_seconds(detail)
                # Honor Retry-After, but do not hold a BB run indefinitely.
                # Retry only a documented 429 refusal with zero output and no
                # accepted submission; all ambiguous outcomes fail closed.
                if (delay is not None and delay <= 180 and rate_retries < 2
                        and not submitted and not rendered and not final
                        and new_conversation_id is None):
                    await asyncio.sleep(delay + 1)
                    await self.do_prompt(request_id, sid, blocks,
                                         rate_retries=rate_retries + 1)
                    return
                raise RuntimeError(detail)
            if not new_conversation_id and not session.get("conversation_id"):
                raise RuntimeError("ChatGPT Web replied without a conversation ID")
            if new_conversation_id:
                session["conversation_id"] = new_conversation_id
            session["adapter_policy_hash"] = policy_hash
            session.pop("pending_turn", None)
            save_session(session)
            # Prefer the provider's completed message, falling back to the
            # most recent stream snapshot when no final message was included.
            # Never concatenate both: replacements are not append-only deltas.
            canonical = final if final else rendered
            if canonical:
                # The last streamed commentary might not have ended in a
                # newline. Keep the final answer as its own Markdown block.
                if any(key.startswith("commentary-") for key in activity_titles):
                    canonical = "\n\n" + canonical
                await self.update(sid, canonical)
            for raw_id in activity_rows:
                if raw_id == "request":
                    call_id = activity_prefix + "-request"
                    title = "ChatGPT Web · Response verified"
                elif raw_id == "preview":
                    call_id = activity_prefix + "-preview"
                    title = "Live answer preview · Final answer delivered"
                elif raw_id.startswith("commentary-"):
                    call_id = activity_prefix + "-" + hashlib.sha256(raw_id.encode()).hexdigest()[:12]
                    title = activity_titles[raw_id]
                else:
                    call_id = activity_prefix + "-tool-" + hashlib.sha256(raw_id.encode()).hexdigest()[:12]
                    title = activity_titles[raw_id]
                await self.activity(sid, call_id, title, status="completed")
            available_images = generated_media(session.get("conversation_id"))
            embedded = embedded_image_assets(render_web_elements(
                canonical, rich_ui=RICH_UI_READY.is_file(),
                image_assets=frozenset(available_images)))
            fresh_images = [image for image in available_images
                            if image not in previous_images and image not in embedded]
            if fresh_images:
                await self.update(sid, markdown_images(fresh_images))
            elif not rendered.strip() and not final.strip():
                await self.update(sid, "The provider returned no text or generated image. Check the underlying ChatGPT conversation or retry.")
            await self.emit({"jsonrpc": "2.0", "id": request_id,
                             "result": {"stopReason": "end_turn"}})
        except asyncio.CancelledError:
            for raw_id in activity_rows:
                call_id = (activity_prefix + "-request" if raw_id == "request" else
                           activity_prefix + "-preview" if raw_id == "preview" else
                           activity_prefix + ("-" if raw_id.startswith("commentary-") else "-tool-") +
                           hashlib.sha256(raw_id.encode()).hexdigest()[:12])
                await self.activity(sid, call_id, activity_titles[raw_id], status="cancelled")
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await self.emit({"jsonrpc": "2.0", "id": request_id,
                             "result": {"stopReason": "cancelled"}})
        except Exception:
            for raw_id in activity_rows:
                call_id = (activity_prefix + "-request" if raw_id == "request" else
                           activity_prefix + "-preview" if raw_id == "preview" else
                           activity_prefix + ("-" if raw_id.startswith("commentary-") else "-tool-") +
                           hashlib.sha256(raw_id.encode()).hexdigest()[:12])
                await self.activity(sid, call_id, activity_titles[raw_id], status="failed")
            raise
        finally:
            self.processes.pop(sid, None)
            self.running.pop(sid, None)

    async def dispatch(self, msg: dict[str, Any]):
        method = msg.get("method")
        params = msg.get("params") or {}
        reqid = msg.get("id")
        notification = "id" not in msg
        try:
            if method == "initialize":
                result = {"protocolVersion": 1, "agentCapabilities": {
                    "loadSession": True, "promptCapabilities": {"image": False,
                    "audio": False, "embeddedContext": False}},
                    "authMethods": [],
                    "agentInfo": {"name": "chatgpt-web-acp",
                                  "title": "ChatGPT Web CLI", "version": "0.1.0"}}
            elif method in ("session/new", "session/load", "session/resume"):
                if method == "session/new":
                    # Check for an accepted previous POST BEFORE any model
                    # catalog request: recovery needs no new model selection.
                    prior = accepted_session_for_cwd(params.get("cwd"))
                    recovery = (isinstance(prior, dict) and
                                isinstance(prior.get("pending_turn"), dict) and
                                prior["pending_turn"].get("accepted") is True)
                    catalog = None if recovery else self.catalog()
                    sid = uuid.uuid4().hex
                    if recovery:
                        model = prior.get("model")
                        mode = prior.get("mode")
                    else:
                        ids = [m["modelId"] for m in catalog]
                        model = "gpt-6" if "gpt-6" in ids else ids[0]
                        mode = "thinking"
                    session = {"sessionId": sid, "conversation_id": None,
                               "cwd": params.get("cwd"),
                               "model": model, "mode": mode}
                    if prior is not None:
                        for key in ("pending_turn", "conversation_id", "model",
                                    "mode", "adapter_policy_hash"):
                            if key in prior:
                                session[key] = prior[key]
                    self.sessions[sid] = session
                    save_session(session)
                else:
                    sid = params["sessionId"]
                    session = load_session(sid)
                    self.sessions[sid] = session
                    recovery = (isinstance(session.get("pending_turn"), dict) and
                                session["pending_turn"].get("accepted") is True)
                    if not recovery:
                        self.catalog()
                if isinstance(params.get("cwd"), str):
                    session["cwd"] = params["cwd"]
                if recovery:
                    presentation = self.pending_recovery_ui(session)
                else:
                    self.normalize_session(session)
                    presentation = {"models": self.model_state(session["model"]),
                                    "configOptions": self.config_options(session)}
                save_session(session)
                if method == "session/load" and not recovery:
                    await self.replay(sid, session)
                result = {"sessionId": sid, **presentation}
            elif method in ("session/set_model", "session/set_config_option"):
                sid = params["sessionId"]
                if sid not in self.sessions:
                    self.sessions[sid] = load_session(sid)
                session = self.sessions[sid]
                if (isinstance(session.get("pending_turn"), dict) and
                        session["pending_turn"].get("accepted") is True):
                    raise ValueError("Model settings are locked while recovering the accepted turn")
                self.catalog()
                if sid in self.running:
                    raise ValueError("Cannot change model or reasoning while prompting")
                config_id = ("model" if method == "session/set_model"
                             else params.get("configId"))
                value = (params.get("modelId") if method == "session/set_model"
                         else params.get("value"))
                self.normalize_session(session)
                if config_id == "model":
                    if value not in self.variants:
                        raise ValueError(f"Model {value!r} is not available")
                    session["model"] = value
                    self.normalize_session(session)
                elif config_id == "mode":
                    if value not in self.variants[session["model"]]:
                        raise ValueError(f"Mode {value!r} not supported by {session['model']}")
                    session["mode"] = value
                else:
                    raise ValueError(f"Unsupported config option: {config_id}")
                save_session(session)
                result = {"models": self.model_state(session["model"]),
                          "configOptions": self.config_options(session)}
            elif method == "session/prompt":
                sid = params["sessionId"]
                if sid not in self.sessions:
                    self.sessions[sid] = load_session(sid)
                if sid in self.running:
                    raise ValueError("Session already has a prompt in progress")
                self.running[sid] = asyncio.create_task(self.prompt_safe(reqid, sid, params.get("prompt", [])))
                return
            elif method == "session/cancel":
                sid = params["sessionId"]
                task = self.running.get(sid)
                if task:
                    task.cancel()
                result = {}
            elif method == "session/close":
                sid = params["sessionId"]
                task = self.running.get(sid)
                if task:
                    task.cancel()
                self.sessions.pop(sid, None)
                result = {}
            else:
                raise NotImplementedError(f"Unsupported ACP method: {method}")
            if not notification:
                await self.emit({"jsonrpc": "2.0", "id": reqid, "result": result})
        except Exception as exc:
            if not notification:
                await self.emit({"jsonrpc": "2.0", "id": reqid,
                                 "error": {"code": -32601 if isinstance(exc, NotImplementedError) else -32603,
                                           "message": str(exc)[:1000]}})

    async def prompt_safe(self, reqid: Any, sid: str, blocks: list):
        try:
            await self.do_prompt(reqid, sid, blocks)
        except Exception as exc:
            self.running.pop(sid, None)
            await self.emit({"jsonrpc": "2.0", "id": reqid, "error":
                             {"code": -32603, "message": str(exc)[:1500]}})


async def main():
    server = Server()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=4_000_000)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            await server.dispatch(obj)
        except Exception as exc:
            print(f"ChatGPT Web ACP malformed message: {type(exc).__name__}", file=sys.stderr)
    for task in list(server.running.values()):
        task.cancel()
    if server.running:
        await asyncio.gather(*server.running.values(), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
