#!/usr/bin/env python3
"""Reject private files, credentials and device-specific content before publishing."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 5_000_000
BANNED_FILES = re.compile(
    r"(^|/)(?:\.env[^/]*|\.netrc|\.npmrc|\.pypirc|Cookies|Local State|"
    r"auth\.json|credentials?\.json|chrome-profile|recovery-jobs|token\.json|"
    r"id_rsa|id_ed25519|[^/]*\.pem|[^/]*\.key)$", re.I,
)
HOME_PATH = re.compile(r"/home/(?!USER\b|example\b)[A-Za-z][\w.-]+/", re.I)
PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
)
CREDENTIAL = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{25,}|gh[pousr]_[A-Za-z0-9_]{28,}|"
    r"github_pat_[A-Za-z0-9_]{25,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)"
)
PERSONAL_LABELS = re.compile(
    r"\b(?:" + "|".join(
        ("phili" + "pedia", "tachi" + "bana", "mi" + "rai", "qw" + "qc")
    ) + r")\b", re.I
)
PATTERNS = (
    (HOME_PATH, "personal home path"),
    (PRIVATE_IP, "private or CGNAT IP address"),
    (CREDENTIAL, "credential-like content"),
    (PERSONAL_LABELS, "private project or device label"),
)

def inspect_path(root: Path, name: str) -> list[str]:
    """Check every text-like tracked artifact, regardless of extension."""
    path = root / name
    if path.is_symlink():
        return [f"{name}: symbolic link not permitted in public sources"]
    if not path.is_file():
        return []
    if BANNED_FILES.search(name):
        return [f"{name}: forbidden private artifact"]
    if path.stat().st_size > MAX_TEXT_BYTES:
        return [f"{name}: oversize artifact requires explicit review"]
    try:
        data = path.read_bytes()
    except OSError:
        return [f"{name}: cannot read tracked file"]
    # Images and other binary assets cannot be searched reliably as text.
    if b"\x00" in data:
        return []
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return [
        f"{name}: {label}"
        for pattern, label in PATTERNS if pattern.search(body)
    ]

def main() -> int:
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).decode("utf-8").rstrip("\0").split("\0")
    errors = []
    for name in names:
        if name:
            errors.extend(inspect_path(ROOT, name))
    if errors:
        print("Public tree audit failed:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    print(f"Public tree audit passed ({len([n for n in names if n])} files)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
