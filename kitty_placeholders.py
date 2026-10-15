"""Persistently placed pixel images via Kitty Unicode placeholders.

The Kitty graphics protocol allows a *virtual placement* (U=1) to be tied to
ordinary Unicode text cells. Unlike cursor-positioned images, those cells
survive TUI redraw, terminal scrollback and prompt_toolkit reflow.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from terminal_native import geometry, load_png

# kitty/gen/rowcolumn-diacritics.txt, Unicode 6.0 subset, first 72 entries.
DIACRITICS = tuple(chr(int(value, 16)) for value in (
    "0305 030D 030E 0310 0312 033D 033E 033F 0346 034A 034B 034C "
    "0350 0351 0352 0357 035B 0363 0364 0365 0366 0367 0368 0369 "
    "036A 036B 036C 036D 036E 036F 0483 0484 0485 0486 0487 0592 "
    "0593 0594 0595 0597 0598 0599 059C 059D 059E 059F 05A0 05A1 "
    "05A8 05A9 05AB 05AC 05AF 05C4 0610 0611 0612 0613 0614 0615 "
    "0616 0617 0657 0658 0659 065A 065B 065D 065E 06D6 06D7 06D8"
).split())
PLACEHOLDER = "\U0010eeee"
ESC = bytes((27,))
ST = ESC + bytes((92,))


def dimensions(image_width: int, image_height: int, available_width: int) -> tuple[int, int]:
    max_columns = min(72, max(6, available_width - 6))
    return geometry(image_width, image_height, max_columns, 28)


def image_id_for_file(path: str | Path) -> int:
    source = Path(path).expanduser().resolve()
    state = source.stat()
    payload = f"{source}:{state.st_size}:{state.st_mtime_ns}".encode()
    # Reserve non-zero image IDs; same file keeps the same ID within a session.
    return int.from_bytes(hashlib.blake2s(payload, digest_size=3).digest(), "little") or 1


def id_to_rgb(image_id: int) -> tuple[int, int, int]:
    if not 1 <= image_id <= 0xffffff:
        raise ValueError("Invalid image ID")
    return image_id & 255, (image_id >> 8) & 255, (image_id >> 16) & 255


def placeholder_rows(image_id: int, columns: int, rows: int) -> list[str]:
    if not (1 <= columns <= len(DIACRITICS) and 1 <= rows <= len(DIACRITICS)):
        raise ValueError("Placeholder dimensions out of range")
    return [
        "".join(PLACEHOLDER + DIACRITICS[row] + DIACRITICS[column]
                for column in range(columns))
        for row in range(rows)
    ]


def virtual_placement_packets(png: bytes, image_id: int, columns: int, rows: int) -> list[bytes]:
    if not png.startswith(bytes((137,80,78,71,13,10,26,10))):
        raise ValueError("Only PNG is supported for virtual placement")
    if not (1 <= columns <= 72 and 1 <= rows <= 45):
        raise ValueError("Image dimensions outside safe terminal bounds")
    if not 1 <= image_id <= 0xffffff:
        raise ValueError("Invalid image identifier")
    encoded = base64.b64encode(png)
    if len(encoded) > 32_000_000:
        raise ValueError("Image transfer exceeds maximum")
    packets = []
    for index, offset in enumerate(range(0, len(encoded), 4096)):
        extra = int(offset + 4096 < len(encoded))
        options = (f"a=T,f=100,t=d,U=1,i={image_id},c={columns},r={rows},q=2,m={extra}"
                   if index == 0 else f"m={extra}")
        packets.append(ESC + b"_G" + options.encode("ascii") + b";" +
                       encoded[offset:offset + 4096] + ST)
    return packets


def prepare_virtual_image(path: str | Path, available_width: int) -> tuple[int, int, int, list[bytes]]:
    png, (width, height) = load_png(path)
    cols, rows = dimensions(width, height, available_width)
    ident = image_id_for_file(path)
    return ident, cols, rows, virtual_placement_packets(png, ident, cols, rows)
