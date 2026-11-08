# Native ChatGPT rendering acceptance — 2026-11-08

## Verdict

Exact native parity is **not accepted**. The component fixture passes structural and
contrast gates at 520/1500 CSS px, light/dark, DPR 1. That is component evidence,
not proof of whole-message or native ChatGPT parity. Existing live progress rendering
was observed again, but the existing badge acceptance thread contains plain text,
so it does not establish live badge integration.

## Supported API audit

Audited installed `@get-bb/plugin-sdk` **0.6.94**, BB **0.48.0**. The contract is
`bundled-types/bb-plugin-sdk-app.d.ts` in that pinned SDK package:

- `PluginMessageDirectiveProps` (line 19320): attributes, original source, message
  identity, and workspace opener. Leaf content inside assistant Markdown; no parent
  message renderer, paragraph/heading stylesheet, tool header, or grouping callback.
- `ExperimentalCodeFenceRendererProps` (19353): code, language, streaming/closed/
  unclosed status, index, message, and `Original`. Registration claims explicit
  languages; it is not a provider-scoped whole-Markdown renderer. Keep ordinary
  code on `Original` rather than hijacking languages used by unrelated providers.
- `PluginTimelineRendererRegistration` (21732): only this plugin's extension kinds
  or generic `tool` bodies of its own registered providers. Its declaration says:
  "Core kinds ... always use the core renderers" and "The row's header ... stays
  host-rendered". The rich-UI plugin does not register the ChatGPT ACP provider,
  so claiming `tool` here would not replace that external provider's tool rows.
- `PluginAppSlots` (21800 onward): no `messageRenderer` or complete timeline
  replacement registration. A timeline **body** slot is not that API.
- `ThreadChatProps` (22557): thread identity, variant/layout, permission policy,
  className, leadingContent, messageActions. No renderMessage/renderRow override.
- `experimental_sourceCodeRenderer` can replace shared source views; it is not a
  provider-specific Markdown or chat-layout override. No global replacement added.

The installed plugin-authoring reference `frontend-renderer-slots.md` agrees with
these ownership limits. No minified host internals, ancestor selectors, mutation
observers, or global preference changes are used by this change.

## Specific remaining mismatches

The supplied side-by-side images show different font scale, reading width, heading
spacing, input chrome, progress presentation, and tool aggregation. Their windows
have unequal widths and unknown zoom/DPR, so they cannot establish pixel-equality
thresholds for either requested viewport or dark mode.

A new read-only live BB capture measures the `Results` H2 at **15px**, with **0px**
top and **8px** bottom margin at both requested widths/themes. It also observes the
existing 73% progress bar. The original image's heading appears larger, but its
uncontrolled scale prevents an honest exact CSS-pixel target. The plugin cannot
change host Markdown heading margins through the audited slots. A directive can
style a new, explicitly emitted heading leaf; it cannot fix existing headings or
surrounding paragraphs. Such conversion is intentionally not guessed from titles.

Tool arguments/output remain structured through the existing ACP adapter; this
change does not rewrite them. Tool headers, compact summaries, grouping, elapsed
labels, and expansion controls remain host-owned. Exact tool detail presentation
is not independently accepted by these component screenshots.

Final-answer browser snapshots still reconcile before one canonical ACP append.
The adapter deliberately buffers mutable final answers to avoid duplication.
Public commentary may append observed deltas and publish explicit corrections.
The literal-code fix protects complete/interrupted code in each input snapshot;
it does not create a mutable ACP message API or fix cross-notification commentary
revisions. The original accepted failed turn was inspected read-only, not retried,
resent, deleted, or restarted.

Charts with only JSX references cannot be reconstructed from absent series data.
Fallbacks retain available literal title/type metadata and explicitly disclose
unavailable chart data; they never evaluate expressions or invent plotted values.

## Implemented supported alternatives

- A `chatgpt-metric` leaf preserves a recognized label/value row instead of flattening
  the timing pair into paragraphs. Only bounded plain literal values are promoted;
  quotes, braces, and backslashes use the existing plain fallback.
- Status badges wrap long labels and use theme-aware BB palette tokens. All five
  tones pass 4.5:1 small-text contrast in the Chromium fixture. These token-based
  colors are an accessibility improvement, not proof of native ChatGPT colors.
- Complete, interrupted, tilde, and longer Markdown fences remain literal. A
  ChatGPT CodeBlock containing backticks gets a longer outer fence; malformed
  language labels cannot insert new Markdown. Inline CodeBlock examples stay code.
- Existing opt-in/terminal behavior remains: metric directives require rich_ui;
  plain rendering remains available. No generated app/script is executed.

## Exact acceptance gates

All gates below are required before calling the objective Done:

1. Identical source content and source-message identity for native and BB captures;
   fixed viewport 520/1500, theme light/dark, zoom 100%, DPR 1, font readiness, and
   complete/streaming/interrupted states. Capture unedited originals and hashes.
2. Compare heading font, line-height, margins, paragraph/list spacing, reading width,
   code font/highlighting/copy behavior, badge geometry/colors, tool headers/body/
   grouping, and charts against corresponding native captures. Any unavailable
   component/state or mismatch fails exact acceptance; no aggregate score hides it.
3. For a strict pixel gate, zero differing pixels after documented alignment of
   the same content region. If platform text rasterization requires a tolerance,
   record and separately approve it before running; do not call it exact parity.
4. Component gates: no horizontal overflow; no label/value overlap; exact progress
   numeric/visual proportion; invalid-source fallback; bounded literal rendering;
   all badge tones at least 4.5:1. These are necessary, not sufficient for parity.
5. Accepted-turn tests must prove no duplicate POST, persisted request identity,
   read-only reconciliation, and canonical final content without duplication.
   End-to-end real accepted-turn recovery remains a separate runtime gate.
6. Independent review of the matched native/BB evidence, plus verified installation
   of the exact reviewed revision. A build or component fixture alone cannot pass.

## Reproduction

Run `npm ci` in `bb-plugin-rich-ui`, then `npm run qa:build`. The builder uses
`bb plugin build` so the fixture loads the exact generated plugin CSS and renders
actual React component source. Python `playwright` and a compatible Chromium are
required externally; no subscription request is made.

Run `python3 bb-plugin-rich-ui/qa/review.py --chromium "$CHROMIUM" --out "$EVIDENCE"`.
Use `--host` for a non-default BB URL. This creates four real component screenshots
and a machine-readable report with measured layout, fill ratios and contrast.
It serves only a temporary loopback fixture, then closes its own browser/server.
The fixture headings are harness labels, not evidence of host heading changes.

Run `python3 bb-plugin-rich-ui/qa/host_review.py --thread "$EXISTING_THREAD" --chromium
"$CHROMIUM" --out "$EVIDENCE"` for four read-only host captures and measurements.
It blocks non-read HTTP methods, never submits a prompt, and changes a theme class
only in an ephemeral page. It does not persist appearance preferences or install
anything. Live event delivery/telemetry may be absent because mutations are blocked.

## Concrete upstream request (draft; not submitted)

Title: **Provider-scoped assistant message and timeline presentation renderers**.

Request an `experimental_messageRenderer` registration scoped to provider ID,
with canonical content/parts, stable message/turn identity, role, streaming status,
revision-aware snapshots, attachment/citation capabilities, and `Original` fallback.
Permit plugins to own a scoped Markdown root and render heading/code spacing without
editing host DOM. Preserve host permission boundaries, selection/copy, virtualization,
error isolation, accessibility, and lifecycle cleanup.

Extend provider-owned timeline rendering with an optional header/group renderer
(label, status, elapsed time, detail body, expansion state), again with `Original`.
Provide a supported fixture/replay surface for recorded messages/rows, including
streaming replacement and interrupted states, without invoking a model or editing
production storage. Add a revision/update bridge event if ACP append-only chunks
remain insufficient. Each new public API should use the experimental prefix and
receive guide/inventory entries according to BB's authoring contract.

Acceptance for that request: a provider-scoped plugin changes only its own messages
and tool groups, switches off cleanly to `Original`, cannot widen permissions, and
passes the matched-source visual matrix plus replacement/stream-interruption tests.
No public issue or PR was opened by this audit.
