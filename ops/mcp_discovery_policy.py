#!/usr/bin/env python3
"""Enable read-only MCP discovery aliases, preserving other policy.

Some MCP gateways reserve discovery names before a facade receives them.
The already implemented aliases avoid those reserved names through execute_tool.
No credentials, execution tools or network access are added by this change.
"""
import argparse
import json
from pathlib import Path

ALIASES = ("find_mcp_tools", "get_mcp_tool_schema")


def updated_policy(policy):
    value = json.loads(json.dumps(policy))
    entry = value.setdefault("chatgpt", {})
    tools = entry.setdefault("tools", [])
    for alias in ALIASES:
        if alias not in tools:
            tools.append(alias)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    original = args.policy.read_bytes()
    policy = json.loads(original)
    updated = updated_policy(policy)
    changed = updated != policy
    if changed and args.apply:
        # The facade reads this bind-mounted file on each call. Keep its inode;
        # the backup and byte comparison protect unrelated administrative edits.
        backup = args.policy.with_name(args.policy.name + ".before-discovery-aliases")
        if not backup.exists():
            backup.write_bytes(original)
            backup.chmod(0o600)
        with args.policy.open("r+", encoding="utf-8") as stream:
            if stream.read().encode("utf-8") != original:
                raise RuntimeError("Policy changed concurrently; retry after inspection")
            stream.seek(0)
            stream.write(json.dumps(updated, indent=2) + "\n")
            stream.truncate()
    print(json.dumps({"changed": changed, "applied": changed and args.apply,
                      "server": "chatgpt", "read_only_aliases": list(ALIASES)}))


if __name__ == "__main__":
    main()
