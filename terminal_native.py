"""Render high-quality PNG previews through the Kitty graphics protocol.

The original file is preserved. For 100% zoom, use the desktop viewer.
"""
from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path
from typing import BinaryIO, Mapping

from PIL import Image, ImageOps

ESC = bytes([27])
ST = ESC + bytes([92])
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])
FORMATS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
Image.MAX_IMAGE_PIXELS = 45000000


def supports_native(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    if env.get("TMUX") or env.get("STY"):
        return False  # Multiplexers vary in support; use /hd to force.
    term = env.get("TERM", "").lower()
    program = env.get("TERM_PROGRAM", "").lower()
    return (term.startswith(("xterm-ghostty", "xterm-kitty"))
            or program in ("ghostty", "kitty", "wezterm")
            or ("GHOSTTY_RESOURCES_DIR" in env and term != "dumb"))


def load_png(path: str | Path, limit: int = 2200) -> tuple[bytes, tuple[int, int]]:
    source = Path(path).expanduser()
    if not source.is_file() or source.suffix.lower() not in FORMATS:
        raise ValueError("Image file missing or unsupported")
    if source.stat().st_size > 45000000:
        raise ValueError("Image is too large")
    with Image.open(source) as original:
        original.seek(0)
        frame = ImageOps.exif_transpose(original)
        frame.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        output = BytesIO()
        frame.save(output, "PNG")
        return output.getvalue(), frame.size


def geometry(width: int, height: int, columns: int, rows: int) -> tuple[int, int]:
    if min(width, height, columns, rows) <= 0:
        raise ValueError("Invalid size")
    cols = min(columns, max(1, round(rows * width * 2 / height)))
    height_rows = min(rows, max(1, round(cols * height / width / 2)))
    return cols, height_rows


def render_native(
    path: str | Path, output: BinaryIO, columns: int, rows: int,
    tmux_passthrough: bool = False,
) -> tuple[int, int]:
    png, dimensions = load_png(path)
    if not png.startswith(PNG_MAGIC):
        raise ValueError("Invalid PNG")
    cols, height_rows = geometry(*dimensions, columns, rows)
    encoded = base64.b64encode(png)
    if len(encoded) > 32000000:
        raise ValueError("Encoded image too large")
    for index, start in enumerate(range(0, len(encoded), 4096)):
        remaining = int(start + 4096 < len(encoded))
        header = (f"a=T,f=100,t=d,c={cols},r={height_rows},C=1,q=2,m={remaining}"
                  if index == 0 else f"m={remaining}")
        packet = ESC + b"_G" + header.encode() + b";" + encoded[start:start+4096] + ST
        if tmux_passthrough:
            packet = ESC + b"Ptmux;" + packet.replace(ESC, ESC+ESC) + ST
        output.write(packet)
    output.write(bytes([13, 10]) * height_rows)
    output.flush()
    return cols, height_rows
