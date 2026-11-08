# ACP integration

bb_acp.py exposes the authenticated ChatGPT Web CLI to ACP-compatible applications.

## Local setup

Authenticate using ./run login and ./run auth, then run:

~~~sh
python3 bb_acp.py
~~~

Example ACP agent configuration (replace the path):

~~~json
{
  "id": "chatgpt-web",
  "displayName": "ChatGPT Web",
  "command": "python3",
  "args": ["/absolute/path/to/chatgpt-web/bb_acp.py"],
  "env": {}
}
~~~

The adapter runs locally. Override its CLI launcher with CHATGPT_WEB_ACP_CLI. State and data follow XDG paths.

## Remote execution

The adapter does not automatically connect to remote hosts. Set up any SSH
execution through your trusted ACP host, with host-key validation. Do not expose
the local daemon socket or publish private SSH targets.

## Models

The adapter reads the active account model catalog and offers recognized model families with only supported modes. New unrecognized model IDs can appear as a single Default mode. The adapter does not change the global model setting.

## Rate limits

On a definite provider rejection carrying a valid Retry-After, the adapter retains the ACP turn and original prompt. It updates a single progress entry every second, retries when eligible, and respects cancellation. It will not blindly retry ambiguous submissions that may have reached the service. It limits repeated rate-limit attempts.

This recovery is per running adapter process. It does not persist an in-flight retry across a host restart. Account rate limits still apply.

Exact countdown display depends on how the ACP host renders progress updates; verify it in the real UI.

## Capability boundary

The ACP adapter has no native ACP shell, filesystem or workspace tools. ChatGPT connected tools, if present in the authenticated account, remain separate and must be discovered at runtime. Model prose does not prove execution.

CHATGPT_WEB_ACP_POLICY_FILE supplies an optional operator policy file. An explicitly configured missing/empty/oversized policy fails safely. CHATGPT_WEB_ACP_IMAGE_BRIDGE configures an optional trusted local Python worker. CHATGPT_WEB_ACP_IMAGE_ENDPOINT and CHATGPT_WEB_ACP_WORKER_IMAGE_ENDPOINT configure optional image-serving routes. Without a trusted relative HTTP route, the adapter reports saved image IDs instead of inventing working image URLs.
