#!/usr/bin/env python3
"""Audit a public checkout for common personal paths and credential leaks."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BANNED_FILES = re.compile(
    r"(^|/)(?:\.env[^/]*|Cookies|Local State|auth\.json|credentials?\.json|"
    r"chrome-profile|recovery-jobs|token\.json|id_rsa|id_ed25519|"
    r"[^/]*\.pem|[^/]*\.key)$", re.I,
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
TEXT_EXTENSIONS = {".py", ".md", ".js", ".json", ".yaml", ".yml", ".sh", ".toml", ".txt", ".ts", ".tsx", ".css", ".html", ".svg", ".cjs", ".mjs", ".service", ".timer"}
PERSONAL_LABELS = re.compile(r"\b(?:" + "|".join(("phili" + "pedia", "tachi" + "bana", "mi" + "rai", "qw" + "qc")) + r")\b", re.I)

def main() -> int:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, text=True,
    ).splitlines()
    errors = []
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        if BANNED_FILES.search(name):
            errors.append(f"{name}: forbidden private artifact")
            continue
        if path.suffix not in TEXT_EXTENSIONS and path.name not in {"chatgpt-web", "run"}:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        for pattern, label in (
            (HOME_PATH, "personal home path"),
            (PRIVATE_IP, "private or CGNAT IP address"),
            (CREDENTIAL, "credential-like content"),
            (PERSONAL_LABELS, "private project or device label"),
        ):
            if pattern.search(body):
                errors.append(f"{name}: {label}")
    if errors:
        print("Public tree audit failed:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    print(f"Public tree audit passed ({len(files)} files)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
