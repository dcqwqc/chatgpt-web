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


def model_rows(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise RuntimeError("ChatGPT Web model catalog not available")
    parsed = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("slug")
        if (isinstance(name, str) and MODEL_RE.fullmatch(name)
                and name not in seen and not name.startswith("chatgpt_alpha_")):
            seen.add(name)
            title = str(item.get("title") or name)[:80]
            variant = next((suffix for suffix in ("instant", "thinking", "t-mini", "mini")
                            if name.endswith("-" + suffix)), None)
            label = title + (" — " + variant.title() if variant else " (Default)")
            parsed.append({"modelId": name, "name": label})
    if not parsed:
        raise RuntimeError("No models available in the connected ChatGPT account")
    return parsed


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
        # Keep the model names/slugs from the same single catalog snapshot.
        self.model_cache = model_rows(details)
        return self.model_cache

    def reasoning_options(self, model: str) -> list[dict[str, str]]:
        details = self.model_capabilities_cache.get(model, {})
        raw = details.get("efforts", [])
        efforts = [
            {"value": item["value"], "name": item.get("name") or item["value"]}
            for item in raw if isinstance(item, dict)
            and item.get("value") in ("medium", "high")
        ]
        if efforts:
            return efforts
        reasoning_type = details.get("reasoning_type")
        if reasoning_type == "none":
            return [{"value": "none", "name": "None"}]
        if reasoning_type == "reasoning":
            return [{"value": "fixed", "name": "Model-controlled"}]
        return [{"value": "auto", "name": "Automatic"}]

    def default_reasoning(self, model: str) -> str:
        return self.reasoning_options(model)[0]["value"]

    def setup_session_reasoning(self, session: dict[str, Any]):
        choices = {x["value"] for x in self.reasoning_options(session["model"])}
        if session.get("reasoning") not in choices:
            session["reasoning"] = self.default_reasoning(session["model"])

    def config_options(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        self.setup_session_reasoning(session)
        return [{
            "id": "model", "name": "Model", "category": "model",
            "type": "select", "currentValue": session["model"],
            "options": [{"value": r["modelId"], "name": r["name"]}
                        for r in self.model_cache or self.catalog()],
        }, {
            "id": "thinking_effort", "name": "Reasoning",
            "category": "thought_level", "type": "select",
            "currentValue": session["reasoning"],
            "options": self.reasoning_options(session["model"]),
        }]

    def model_state(self, current: str):
        items = self.model_cache or self.catalog()
        if current not in [r["modelId"] for r in items]:
            raise ValueError("Selected model no longer available")
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
        prompt = "\n".join(pieces)
        if not session.get("conversation_id"):
            prompt = ("[Adapter capability notice: this ChatGPT Web connection has no "
                      "filesystem, shell, code-execution or BB tool access. Never claim "
                      "to have changed files, run tests, or deployed anything. "
                      "Offer guidance or code as text only.]\n\n" + prompt)
        args = [CLI, "send", session["conversation_id"]] if session.get("conversation_id") else [CLI, "new"]
        args += ["--model", session["model"]]
        self.setup_session_reasoning(session)
        details = self.model_capabilities_cache.get(session["model"], {})
        effort = next((row["thinking_effort"] for row in details.get("efforts", [])
                       if row.get("value") == session["reasoning"]), None)
        if effort is not None:
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
                    default = "gpt-6-thinking" if "gpt-6-thinking" in ids else (
                        "gpt-6" if "gpt-6" in ids else ids[0])
                    session = {"sessionId": sid, "conversation_id": None,
                               "model": default}
                    self.sessions[sid] = session
                    save_session(session)
                else:
                    sid = params["sessionId"]
                    session = load_session(sid)
                    self.sessions[sid] = session
                    self.catalog()
                self.setup_session_reasoning(session)
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
                if config_id == "model":
                    if value not in [r["modelId"] for r in self.model_cache]:
                        raise ValueError(f"Model {value!r} is not available")
                    session["model"] = value
                    self.setup_session_reasoning(session)
                elif config_id == "thinking_effort":
                    allowed = {item["value"] for item in self.reasoning_options(session["model"])}
                    if value not in allowed:
                        raise ValueError(
                            f"Reasoning {value!r} is not supported by {session['model']}"
                        )
                    session["reasoning"] = value
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
