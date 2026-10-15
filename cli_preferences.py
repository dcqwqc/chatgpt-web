"""Local preferences and conservative parsing for the ChatGPT Web CLI fork.

Do not include personal project identifiers or access tokens in the repository.
This file never makes network calls or guesses usage quotas.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

PROJECT_NAME = "CLI"
PROJECT_ID_RE = re.compile(r"^g-p-[a-f0-9]{32}$")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def preferences_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "chatgpt-web" / "settings.json"


def load_preferences() -> dict:
    try:
        obj = json.loads(preferences_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return obj if isinstance(obj, dict) else {}


def save_preferences(**updates: str) -> dict:
    current = load_preferences()
    for key, value in updates.items():
        if key == "project_id":
            if not PROJECT_ID_RE.fullmatch(value):
                raise ValueError("Invalid ChatGPT Project identifier")
        elif key == "model":
            if not MODEL_RE.fullmatch(value):
                raise ValueError("Invalid model slug")
        else:
            raise ValueError("Unknown preference")
        current[key] = value
    path = preferences_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return current


def parse_projects(value: object, name: str = PROJECT_NAME) -> list[dict[str, str]]:
    """Return exact-name Projects from sidebar; never choose a lookalike."""
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError("ChatGPT returned an invalid Project list")
    result = []
    for item in value["items"]:
        if not isinstance(item, dict):
            continue
        gizmo = item.get("gizmo")
        if not isinstance(gizmo, dict):
            continue
        if isinstance(gizmo.get("gizmo"), dict):
            gizmo = gizmo["gizmo"]
        label = gizmo.get("display")
        if not isinstance(label, dict):
            continue
        project_id = gizmo.get("id")
        if label.get("name") == name and isinstance(project_id, str) and PROJECT_ID_RE.fullmatch(project_id):
            result.append({"id": project_id, "name": name})
    return result


def parse_models(value: object) -> list[dict[str, str]]:
    if not isinstance(value, dict) or not isinstance(value.get("models"), list):
        raise ValueError("ChatGPT model catalog is unavailable")
    results = []
    for item in value["models"]:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not MODEL_RE.fullmatch(slug):
            continue
        # The current website reports enable_infer=False even for the
        # visible consumer models. Do not use it as a selectable flag.
        if item.get("is_work_mode_model") or slug == "research":
            continue
        results.append({"slug": slug, "title": str(item.get("title") or slug)[:80]})
    return results


def web_usage_status() -> dict:
    """ChatGPT Web doesn't expose trustworthy numeric remaining counts."""
    return {
        "service": "ChatGPT Web (ordinary chats)",
        "remaining_messages": None,
        "reset_at": None,
        "available": "not reported",
        "note": "ChatGPT Web does not provide a reliable remaining-message counter via this client. Codex/Work usage is a different allowance.",
    }

def summarize_codex_usage(payload: object) -> dict:
    """Return window percentages only; no account identifiers."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rate_limit"), dict):
        raise ValueError("Codex usage response has no rate limit")
    rate = payload["rate_limit"]
    result = {"service": "Codex / Work (separate allowance)", "windows": {}}
    for key in ("primary_window", "secondary_window"):
        window = rate.get(key)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent")
        reset = window.get("reset_after_seconds")
        if not isinstance(used, (int, float)) or isinstance(used, bool) or not 0 <= used <= 100:
            continue
        result["windows"][key] = {
            "used_percent": round(used, 1),
            "remaining_percent": round(100 - used, 1),
            "reset_after_seconds": int(reset) if isinstance(reset, (int, float)) and reset >= 0 else None,
        }
    return result
