# BB provider-scoped rendering: isolated source implementation

## Result and status

BB host source was available locally in a Git checkout whose origin is
`https://github.com/get-bb/bb.git`. An isolated branch was created from release
`desktop-v0.48.0`, base `1a4a1d2` (SDK 0.6.94), matching the installed release.
This replaces the earlier assumption that only installed distribution files were
available. No installed vendor bundle was edited. No shared daemon or active
conversation was modified, no public issue/PR was opened, and nothing was pushed.

The accompanying `bb-provider-renderer.patch` is a real source implementation,
not a claim that the currently installed BB exposes the API. It must be reviewed,
built and adopted as a host change before the live ChatGPT plugin can use it.
A new SDK release/version is required before external distribution; the release
base's 0.6.94 version is not a claim of compatibility with stock 0.6.94.

Source candidate: `912572faee1fd17ace5ac1a98b2ea5699543895f` on
`feat/provider-scoped-renderers-20261108`. All 36 selected regression tests and
app/SDK typechecks pass; eight source screenshots passed the capture checks.
The patch applies cleanly to its release base using a separate temporary Git
index. Full production build and deployed acceptance are untested.

## Smallest upstream extension

One registration: `app.slots.experimental_providerRenderer`, with `id`, one exact
`providerId`, one `surface`, and `component`. Supported surfaces are:

- `assistant-message`: current message identity, text snapshot and streaming flag.
- `tool-header`: current generic tool row identity, plain title and status.
- `tool-group-header`: those same summary fields for **all-tool** bundle and step
  summaries. Mixed groups containing approvals/reasoning/other row types remain
  core-owned, as do all permission/approval headers.

The component receives the resolved provider ID, discriminated snapshot content,
and a stable `Original` component. It can wrap `Original` in its own scoped CSS or
render different content. No wildcard or user-message registration exists. A
registration is explicit opt-in; with no registration the core path is unchanged.
Installing/enabling/disabling a plugin remains available through BB's existing CLI
and SDK lifecycle. No new server/daemon operation or permission setting is added.

Duplicate scopes inside a plugin are rejected at registration. Competing plugins
for a provider/surface resolve to core rather than silently choosing a winner.
A thrown component falls back to current original content and retries on a changed
snapshot. Plugin unload removes the registration through the existing lifecycle.

## Boundaries preserved

Assistant rendering is inside core selection and code-fence context. Attachments,
message actions, permission checks and toolbar remain outside the replacement.
The context-backed Original component keeps its React identity across snapshots;
plugin state and Original subtree state survive ordinary streaming/replacements.
Headers replace only visual summary content **inside the existing disclosure**.
Core retains the focusable control, expansion state, body, authorization and row
ordering. The contract does not pass permission decision callbacks or internal
mutable timeline models. Header components must emit noninteractive phrasing
content; nested controls would conflict with core's disclosure button.

This extension does not expose an entire timeline replacement. It does not alter
group membership, reorder tool rows, make ACP append-only messages mutable, or
replace native message toolbar actions. Those are separate gaps if matched native
captures show they are necessary. The streaming flag alone does not distinguish
completed from interrupted final state; source tests cover the transition to a
nonstreaming snapshot without inventing a finish reason.

## Typography findings

BB source deliberately chooses dense desktop typography:

- `apps/app/src/components/ui/theme.css`: text-sm = 0.8125rem (13px at root16),
  text-base = 0.9375rem (15px), and Inter Variable for the sans family.
- `ConversationMessageContent.tsx`: assistant column uses text-sm/leading-relaxed.
- `markdown-preview.tsx`: Markdown root also specifies text-sm; H2 uses
  text-base, mb-2, mt-4 and first:mt-0. H3 uses text-sm and different margins.
- The narrow-touch typography increase requires **both** width below 768px and a
  coarse pointer. A desktop Chromium viewport narrowed to 520px still uses the
  desktop font scale, explaining why width alone did not increase font sizes.

No supported provider-specific typography setting or message-root stylesheet hook
was found in the pinned SDK/host slot contracts. Global font/theme settings and
content scripts are not scoped alternatives. The new renderer supplies a supported
plugin-owned root, where explicit CSS can style Original's own content subtree.
It does not edit ancestors, use MutationObserver, alter global preferences or
inspect minified host internals.

The source fixture measures H2 15px/body13px before opt-in; the demonstration uses
H2 20px/body15px afterward, while Codex remains H2 15px/body13px at every measured
width/theme. These are **demonstration choices**, not measured native ChatGPT
values. Native font identity, zoom and line-height must still be captured from an
authorized native surface; existing reference images do not establish CSS values.
No native font asset was copied or fabricated.

## Verification and evidence classification

The source patch contains targeted tests for registration validation, provider
isolation, conflict fallback, crash/recovery, stable Original and plugin state,
snapshot replacement/nonstreaming completion, tool disclosure, mixed permission
group exclusion, and actual timeline group call sites. Existing streaming,
row presentation, disclosure and embedded-provider tests run alongside them.
The SDK and app are typechecked through Turbo. UI static preflight is separate
from real screenshot inspection.

`apps/app/qa/provider-renderer` is a reproducible **source fixture**, not a live
production host or a native ChatGPT capture. Its synthetic message/tool content
is identical across two provider contexts. Its loopback Vite server has no
production API proxy; its screenshot runner blocks API requests, waits for fonts,
and captures enabled/disabled at 520/1500 CSS px, DPR1, light/dark. Checks compare
untargeted Codex content and measured styles, source message text, code font and
horizontal overflow. The actual host source renders the messages and tool groups.
No subscription request is needed. The fixture's tool rows are synthetic data,
not evidence that those tools ran against a real thread.

Native capture was not attempted through an unapproved route. The connected Loom
isolated-workspace capability call returned that graphical workspaces are disabled
for remote MCP clients. No setting was enabled, no desktop profile/cookies were
read, and no alternate authenticated CDP endpoint was used. This is a proved native
capture blocker in the current session, not proof that native capture can never
be supported. The original native references remain qualitative evidence only.

## Apply and reproduce

Review the patch in a separate BB checkout based on `desktop-v0.48.0`; verify
`git apply --check` before applying. The patch includes SDK types/collector, host
slot resolution and core call sites, regression tests, Plugin Guide inventory,
API audit entry, and the source fixture with its capture runner.

Follow `apps/app/qa/provider-renderer/README.md` from the patch for dependency,
Turbo typecheck/test and loopback screenshot commands. The matched source release
is important; do not apply this blindly to a newer BB branch. Full host production
build, supported rollout and live verification remain required before adoption.
No shared-daemon restart is authorized by this document.

## Remaining acceptance gates and next dependency

1. Independent source review, including permission-group exclusion and plugin
   lifecycle, then a controlled custom BB build using the reviewed revision.
2. Verify the exact source revision in an isolated host before any production
   rollout. Install an opt-in plugin against the new SDK; verify ordinary providers
   and disable/unload fallback. Current source tests do not prove deployed behavior.
3. Obtain read-only screenshots and computed styles of the **same native message**
   through an authorized isolated authenticated capture route. Do not resend an
   accepted prompt or use the desktop as a fallback.
4. Compare same message, viewport, zoom, DPR, theme and fonts at 520/1500; include
   streaming replacement and interruption states, code copy and tool disclosure.
5. Require independent matched-image review before exact parity acceptance. Keep
   the task incomplete if native captures, toolbar/grouping support or any runtime
   gate is missing. Pixel tolerances must be separately agreed before use.

The immediate platform dependency is adoption of this small BB source extension;
the immediate evidence dependency is an authorized paired native capture route.
