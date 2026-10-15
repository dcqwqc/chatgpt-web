"""Local image-use adapter for the ChatGPT terminal client.

No web service, credential forwarding, or shell invocation is involved.
The structured JSON entry point can later be connected to an MCP service
with explicit user authentication and authorization.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


@dataclass(frozen=True)
class ImageResult:
    output: str
    backend: str
    action: str
    elapsed_seconds: float


def prepare_image_command(
    action: str,
    prompt: str,
    *,
    reference: str | None = None,
    output: str | None = None,
    backend: str = "auto",
) -> tuple[list[str], Path]:
    if action not in {"generate", "edit", "animate"}:
        raise ValueError("Unsupported image action")
    if backend not in {"auto", "web", "codex"}:
        raise ValueError("Invalid backend (auto, web, codex)")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Image prompt must not be empty")
    if len(prompt) > 10000:
        raise ValueError("Image prompt is too long")
    executable = shutil.which("image-use")
    if executable is None:
        raise RuntimeError("image-use is not installed or not on PATH")
    if action == "edit":
        if not reference:
            raise ValueError("Editing requires a reference image")
        ref_path = Path(reference).expanduser().resolve()
        if not ref_path.is_file():
            raise ValueError(f"Image not found: {reference}")
        if ref_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            raise ValueError("Reference must be PNG, JPEG, WebP, or GIF")
    elif reference:
        raise ValueError("References are only supported in edit mode")

    if output:
        destination = Path(output).expanduser().resolve()
    else:
        label = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:45] or "image"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        extension = ".webp" if action == "animate" else ".png"
        destination = (Path.home() / ".local/share/chatgpt-web/images" /
                       f"{stamp}-{label}{extension}").resolve()
    allowed_extensions = {".webp", ".gif"} if action == "animate" else {".png", ".webp", ".jpg", ".jpeg"}
    if destination.suffix.lower() not in allowed_extensions:
        raise ValueError(f"Output must have one of: {', '.join(sorted(allowed_extensions))}")

    # Keep browser-backed media inside the same CLI Project as chat.
    # When Chrome automation is unavailable, Codex can render without adding
    # a ChatGPT Web conversation to the top-level chat history.
    resolved_backend = backend
    if backend == "auto":
        resolved_backend = "web" if shutil.which("chrome-use") else "codex"
    command = [executable]
    if action == "animate":
        command.append("animate")
    command += [prompt, "--backend", resolved_backend, "--out", str(destination), "--no-progress"]
    if resolved_backend == "web":
        command += ["--project", "CLI", "--require-project"]
    if action == "edit":
        command += ["--ref", str(ref_path)]
    return command, destination


def generate_image(
    action: str,
    prompt: str,
    *,
    reference: str | None = None,
    output: str | None = None,
    backend: str = "auto",
    timeout: int = 360,
) -> ImageResult:
    command, destination = prepare_image_command(
        action, prompt, reference=reference, output=output, backend=backend
    )
    if timeout < 10 or timeout > 1200:
        raise ValueError("Timeout must be between 10 and 1200 seconds")
    destination.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    env = {**os.environ, "IMAGE_USE_NO_UPDATE_CHECK": "1"}
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Image generation timed out; check for a late result before retrying") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1400:] or "unknown error"
        raise RuntimeError(f"image-use failed ({result.returncode}): {detail}")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("image-use exited successfully but no output image was found")
    return ImageResult(str(destination), backend, action, round(time.monotonic() - began, 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Structured local image-use adapter for chatgpt-web")
    parser.add_argument("action", choices=["generate", "edit", "animate"])
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--reference")
    parser.add_argument("--output")
    parser.add_argument("--backend", default="auto", choices=["auto", "web", "codex"])
    parser.add_argument("--timeout", type=int, default=360)
    args = parser.parse_args()
    try:
        result = generate_image(
            args.action, args.prompt, reference=args.reference,
            output=args.output, backend=args.backend, timeout=args.timeout,
        )
        print(json.dumps({"ok": True, **vars(result)}))
        return 0
    except (ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
