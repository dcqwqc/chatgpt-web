# ChatGPT Rich UI for BB

Uses BB's supported `messageDirective` slot for progress tracks, status badges,
and literal timing label/value rows. It does not replace the chat timeline.

The adapter emits rich directives only with the local opt-in marker
`~/.local/state/chatgpt-web/bb-native-rich-ui-ready`. Install and verify a plugin
revision supporting all emitted directives before enabling new adapter formatting.
Without the marker the adapter preserves readable plain text. Removing the marker
changes future render events; it does not rewrite recorded messages.

Build with `bb plugin build ./bb-plugin-rich-ui`. Reproducible local Chromium
fixtures: `npm ci` and `npm run qa:build` inside this directory, followed by
`python3 qa/review.py --chromium "$CHROMIUM" --out "$EVIDENCE"`. Supply a locally
installed Chromium and Python Playwright. No model calls or shared process restarts
are required. Fixture screenshots are explicitly distinct from live host evidence.
See [acceptance and SDK audit](../docs/bb-native-rendering-acceptance.md) for the
read-only host capture command, precise limitations, gates, and upstream API draft.

Directive attributes are untrusted bounded text. Invalid values retain source.
The components make no requests and execute no generated markup or scripts.
Theme styles target only plugin-owned badge classes.
