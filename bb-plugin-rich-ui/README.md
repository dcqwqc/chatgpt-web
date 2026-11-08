# ChatGPT Rich UI for BB

Renders ChatGPT progress tracks as a BB-native accessible progress bar using the supported `messageDirective` slot. It does not replace BB's chat timeline, composer, tools, or theme.

The adapter emits `::chatgpt-progress{value="73"}` only after the local opt-in marker `~/.local/state/chatgpt-web/bb-native-rich-ui-ready` exists. Without that marker it emits a readable text progress bar. Install and verify the plugin before enabling the marker. Deleting the marker restores the text fallback on new render events without restarting services; older already-rendered messages are unchanged.

The percent attribute is validated as an integer from 0 through 100. Invalid directives show their original source. No external assets, web requests, or remote code are used.

Build: `bb plugin build ./bb-plugin-rich-ui`. Integration check: send a **new hidden test turn** whose assistant message contains the directive and visually inspect it with a real browser. Do not interrupt active BB turns to deploy.
