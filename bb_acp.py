#!/usr/bin/env python3
"""BB ACP v1 adapter for the authenticated, user-local ChatGPT Web CLI.

The web CLI is a conversational model, not a coding agent: do not claim
filesystem, tool, or shell access. Each ACP session maps to one CLI Project
conversation and has its own model selection.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import uuid
from typing import Any

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


def remove_bb_system_envelope(text: str) -> str:
    """BB passes host/plugin tool instructions inside a tagged text envelope.

    The Web CLI is text-only and has no BB tool runtime. Forwarding this
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
            await self.emit({"jsonrpc": "2.0", "method": "session/update",
                             "params": {"sessionId": sid,
                                        "update": {"sessionUpdate": "agent_message_chunk",
                                                   "content": {"type": "text", "text": text}}}})

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
        for msg in conversation.get("messages", []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("text")
            if not isinstance(content, str) or not content:
                continue
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

    async def do_prompt(self, request_id: Any, sid: str, blocks: list):
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
        if not session.get("conversation_id"):
            prompt = ("[Adapter capability notice: this ChatGPT Web connection has no "
                      "filesystem, shell, code-execution or BB tool access. Never claim "
                      "to have changed files, run tests, or deployed anything. "
                      "Offer guidance or code as text only.]\n\n" + prompt)
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
                if typ == "delta" and isinstance(event.get("text"), str):
                    delta = event["text"]
                    rendered += delta
                    await self.update(sid, delta)
                elif typ == "replace" and isinstance(event.get("text"), str):
                    value = event["text"]
                    if value.startswith(rendered):
                        await self.update(sid, value[len(rendered):])
                    elif rendered and value != rendered:
                        await self.update(sid, "\n\n[Updated response]\n" + value)
                    else:
                        await self.update(sid, value)
                    rendered = value
                elif typ == "message" and event.get("role") == "assistant":
                    if isinstance(event.get("text"), str):
                        final += ("\n" if final else "") + event["text"]
                elif "conversation_id" in event and isinstance(event["conversation_id"], str):
                    new_conversation_id = event["conversation_id"]
            assert process.stderr is not None
            stderr = (await process.stderr.read()).decode("utf8", "replace")[:1500].strip()
            exit_code = await process.wait()
            if exit_code != 0:
                errors.append(stderr or f"CLI exited with code {exit_code}")
            if errors:
                # This specific error occurs before an API request is prepared.
                # Do not auto-retry any ambiguous send failure: duplicates matter.
                if (not rendered.strip() and not final.strip()
                        and any("Chrome request preparation failed" in msg for msg in errors)
                        and explicit_image_intent(user_prompt)):
                    await self.run_image_fallback(sid, user_prompt, session)
                    await self.emit({"jsonrpc":"2.0","id":request_id,
                                     "result":{"stopReason":"end_turn"}})
                    return
                raise RuntimeError("; ".join(errors))
            if not new_conversation_id and not session.get("conversation_id"):
                raise RuntimeError("ChatGPT Web replied without a conversation ID")
            if new_conversation_id:
                session["conversation_id"] = new_conversation_id
                save_session(session)
            if final and final.startswith(rendered):
                await self.update(sid, final[len(rendered):])
            elif final and final != rendered and not rendered:
                await self.update(sid, final)
            fresh_images = [image for image in generated_media(session.get("conversation_id"))
                            if image not in previous_images]
            if fresh_images:
                await self.update(sid, markdown_images(fresh_images))
            elif not rendered.strip() and not final.strip():
                await self.update(sid, "The provider returned no text or generated image. Check the underlying ChatGPT conversation or retry.")
            await self.emit({"jsonrpc": "2.0", "id": request_id,
                             "result": {"stopReason": "end_turn"}})
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await self.emit({"jsonrpc": "2.0", "id": request_id,
                             "result": {"stopReason": "cancelled"}})
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
                    catalog = self.catalog()
                    sid = uuid.uuid4().hex
                    ids = [m["modelId"] for m in catalog]
                    default = "gpt-6" if "gpt-6" in ids else ids[0]
                    session = {"sessionId": sid, "conversation_id": None,
                               "model": default, "mode": "thinking"}
                    self.sessions[sid] = session
                    save_session(session)
                else:
                    sid = params["sessionId"]
                    session = load_session(sid)
                    self.sessions[sid] = session
                    self.catalog()
                self.normalize_session(session)
                save_session(session)
                if method == "session/load":
                    await self.replay(sid, session)
                result = {"sessionId": sid, "models": self.model_state(session["model"]),
                          "configOptions": self.config_options(session)}
            elif method in ("session/set_model", "session/set_config_option"):
                sid = params["sessionId"]
                if sid not in self.sessions:
                    self.sessions[sid] = load_session(sid)
                session = self.sessions[sid]
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
