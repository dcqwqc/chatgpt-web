# Security policy

This experimental client uses authenticated ChatGPT web sessions.

Use GitHub private vulnerability reporting when available. Do not disclose real credentials in public issues.

- Never commit a browser profile, cookies, session snapshots, chat logs, account identifiers, or network captures with authentication headers.
- Complete verification challenges in the normal browser; do not bypass them.
- Use trusted SSH hosts with verified host keys for opt-in remote ACP.
- Keep browser debug ports local. Do not expose ACP stdio or credential caches to the public network.
- Handle uncertain submission outcomes without blindly sending duplicate messages.
- Expect undocumented provider endpoints and rate limits to change.

No vulnerability disclosure SLA or paid support is offered.
