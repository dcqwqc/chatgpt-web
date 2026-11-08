# Contributing

Open an issue before making a large change.

1. Work on a separate branch and keep each patch focused.
2. Preserve original upstream attribution.
3. Never include credentials, account conversations, local hostnames, private IP addresses, or device-specific paths.
4. Add tests for transport failure, uncertain submission, cancellation, and rate limits.
5. Run the Python, Node, BB plugin, and publication checks before opening a PR:

   ```sh
   python3 -m pip install -r requirements.txt
   python3 -m unittest discover -s tests -q
   node --test tests/test_chrome_recipe.js tests/test_composer_cdp.js
   (cd bb-plugin-provider-icons && npm ci && npm test)
   (cd bb-plugin-rich-ui && npm ci && npm run typecheck && npm test)
   python3 scripts/check_public_tree.py
   ```

   TypeScript uses strict checking of project code and skips inspection
   of third-party SDK declaration internals. CI checks three Python versions
   and both BB plugins. Report which tests actually ran and distinguish
   mocked tests from real browser or ACP-host verification.

Upstream licensing is unresolved. Contributions do not imply that upstream content can be relicensed.
