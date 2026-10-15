"""Portable inline terminal images using 24-bit ANSI half-blocks.

Unlike terminal-specific graphics escape protocols, this preview works inside
the prompt-toolkit transcript, tmux and ordinary terminal scrollback.
"""
from __future__ import annotations

from pathlib import Path


def render_preview(path: str | Path, *, max_columns: int = 42, max_rows: int = 20) -> str:
    from PIL import Image, ImageOps
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("Image does not exist")
    if source.stat().st_size > 45_000_000:
        raise ValueError("Image exceeds the 45 MB preview limit")
    if max_columns < 2 or max_rows < 1:
        raise ValueError("Preview dimensions must be positive")
    with Image.open(source) as original:
        original.seek(0)  # The first animation frame.
        picture = ImageOps.exif_transpose(original)
        if picture.width <= 0 or picture.height <= 0:
            raise ValueError("Invalid image dimensions")
        ratio = picture.height / picture.width
        columns = min(max_columns, max(2, round(max_rows * 2 / ratio)))
        rows = min(max_rows, max(1, round(columns * ratio / 2)))
        # Preserve aspect ratio at a 1:2 cell-to-pixel density.
        if rows == max_rows:
            columns = min(columns, max(2, round(rows * 2 / ratio)))
        picture.thumbnail((max_columns * 8, max_rows * 16))
        picture = picture.convert("RGBA")
        background = Image.new("RGBA", picture.size, (21, 20, 19, 255))
        background.alpha_composite(picture)
        pixels = background.convert("RGB").resize(
            (columns, rows * 2), resample=Image.Resampling.LANCZOS
        )
    result: list[str] = []
    for y in range(0, rows * 2, 2):
        fragments = ["  "]
        last_color = None
        for x in range(columns):
            upper = pixels.getpixel((x, y))
            lower = pixels.getpixel((x, y + 1))
            color = (*upper, *lower)
            if color != last_color:
                fragments.append(
                    f"\x1b[38;2;{upper[0]};{upper[1]};{upper[2]}m"
                    f"\x1b[48;2;{lower[0]};{lower[1]};{lower[2]}m"
                )
                last_color = color
            fragments.append("▀")
        fragments.append("\x1b[0m")
        result.append("".join(fragments))
    return "\n".join(result)
