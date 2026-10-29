"""Private, durable CLI-only session history, including local image requests.

ChatGPT Project chats stay in the service; local-only image sessions stay
on disk and are never silently submitted outside the CLI Project.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from datetime import datetime
import re
import tempfile
import threading
import time
from uuid import uuid4

_LOCK = threading.RLock()
_ID = re.compile(r"^local-[a-f0-9]{32}$")
_SERVER_ID = re.compile(r"^[a-zA-Z0-9_-]{8,100}$")
_ROLES = {"user", "assistant", "system", "image"}


def root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "chatgpt-web" / "local-history"


def new_id() -> str:
    return "local-" + uuid4().hex


def _path(session_id: str) -> Path:
    if not _ID.fullmatch(session_id):
        raise ValueError("Invalid local session ID")
    return root() / "sessions" / (session_id + ".json")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _safe_messages(messages: list) -> list[dict[str, str]]:
    output = []
    for message in messages[:1000]:
        if not isinstance(message, dict):
            continue
        role, text = message.get("role"), message.get("text")
        if role in _ROLES and isinstance(text, str):
            entry = {"role": role, "text": text[:150000]}
            until = message.get("rate_limit_until")
            if isinstance(until, (float, int)) and not isinstance(until, bool):
                entry["rate_limit_until"] = float(until)
            output.append(entry)
    return output


def save(
    session_id: str,
    *,
    server_id: str | None,
    title: str,
    messages: list,
    last_image: str | None = None,
) -> dict:
    file = _path(session_id)
    if server_id is not None and not _SERVER_ID.fullmatch(server_id):
        raise ValueError("Invalid ChatGPT conversation ID")
    cleaned = _safe_messages(messages)
    if len(cleaned) > 1000:
        raise ValueError("Too many messages")
    display_title = title.strip()[:180] if isinstance(title, str) else ""
    if display_title in ("", "New conversation", "Untitled"):
        first = next((m["text"] for m in cleaned if m["role"] == "user"), "")
        display_title = first.replace("\n", " ")[:100] or "New image session"
    with _LOCK:
        previous = _read(file) or {}
        payload = {
            "version": 1,
            "session_id": session_id,
            "server_id": server_id,
            "title": display_title,
            "messages": cleaned,
            "last_image": str(last_image or "")[:1200],
            "created_at": previous.get("created_at") or time.time(),
            "updated_at": time.time(),
        }
        _write(file, payload)
        _write(root() / "active.json", {"session_id": session_id})
    return payload


def load(session_id: str) -> dict | None:
    value = _read(_path(session_id))
    if not value or value.get("session_id") != session_id:
        return None
    value["messages"] = _safe_messages(value.get("messages", []))
    server_id = value.get("server_id")
    if server_id is not None and (not isinstance(server_id, str) or not _SERVER_ID.fullmatch(server_id)):
        value["server_id"] = None
    return value


def active() -> dict | None:
    entry = _read(root() / "active.json") or {}
    sid = entry.get("session_id")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        return None
    return load(sid)


def list_recent(limit: int = 150) -> list[dict]:
    folder = root() / "sessions"
    if not folder.is_dir():
        return []
    paths = sorted(folder.glob("local-*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [v for p in paths if (v := load(p.stem)) and v.get("messages")]


def by_server(server_id: str) -> dict | None:
    if not isinstance(server_id, str) or not _SERVER_ID.fullmatch(server_id):
        return None
    matches = [s for s in list_recent() if s.get("server_id") == server_id]
    return max(matches, key=lambda s: s.get("updated_at", 0)) if matches else None


def recover_older_images(limit: int = 100) -> int:
    """Index old generated PNGs without inventing the original prompt.

    Writes image-only metadata and never changes the active conversation.
    """
    images = root().parent / "images"
    if not images.is_dir():
        return 0
    previous = {
        str(Path(item.get("last_image") or "").expanduser())
        for item in list_recent()
    }
    recovered = 0
    for image in sorted(images.iterdir(), key=lambda p: p.stat().st_mtime,
                        reverse=True)[:limit]:
        if (not image.is_file() or image.suffix.lower() not in
            {".png", ".jpg", ".jpeg", ".webp", ".gif"}
            or not re.fullmatch(r"\d{8}-\d{6}-.+", image.stem)):
            continue
        if str(image) in previous:
            continue
        fingerprint = hashlib.sha256(str(image.resolve()).encode()).hexdigest()[:32]
        sid = "local-" + fingerprint
        path = _path(sid)
        if path.exists():
            continue
        title = "Recovered image · " + re.sub(
            r"^\d{8}-\d{6}-", "", image.stem
        ).replace("-", " ")[:80]
        metadata = {
            "version": 1,
            "session_id": sid,
            "server_id": None,
            "title": title,
            "messages": [
                {"role": "system",
                 "text": "Recovered an image saved before local history recording. "
                         "The original image-generation prompt was not preserved."},
                {"role": "image", "text": str(image)},
            ],
            "last_image": str(image),
            "created_at": image.stat().st_mtime,
            "updated_at": image.stat().st_mtime,
        }
        with _LOCK:
            if not path.exists():
                _write(path, metadata)
                recovered += 1
    return recovered


def merge_recent(remote: list[dict], *, legacy: list[dict] | None = None) -> list[dict]:
    """Add local sessions and optional read-only pre-Project cache."""
    recover_older_images()
    rows = []
    seen = set()
    for item in remote:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        rows.append(dict(item))
        seen.add(item["id"])
    for item in list_recent():
        server_id = item.get("server_id")
        if server_id and server_id in seen:
            continue
        rows.append({
            "id": "local:" + item["session_id"],
            "title": item["title"] + ("  [local images]" if not server_id else "  [saved locally]"),
            "update_time": item.get("updated_at", 0),
            "local": True,
        })
        if server_id:
            seen.add(server_id)
    for item in legacy or []:
        sid = item.get("id")
        if isinstance(sid, str) and sid not in seen:
            rows.append({
                "id": "legacy:" + sid,
                "title": str(item.get("title") or "Older chat")[:100] + "  [older cached chat]",
                "update_time": item.get("update_time") or 0,
            })
            seen.add(sid)
    def timestamp(value: object) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    return 0.0
        return 0.0

    rows.sort(key=lambda x: timestamp(x.get("update_time")), reverse=True)
    return rows
