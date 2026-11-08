# BB literal charts, gallery captions and static controls

## Status

Isolated implementation based on `origin/main` at `bc3b919`. Main subsequently
advanced to `927dee4`; this branch does not overwrite, merge or publish that work.
The separate host-renderer patch and installed vendor runtime were not modified.
No shared daemon was restarted, no accepted ChatGPT prompt was resent, and no
original source conversation was altered.

The candidate is implemented, built, typechecked and verified in local component
fixtures plus one real BB assistant message from a deterministic offline provider.
It is not production deployment, independent source approval or exact native parity.
There were no AI model calls, new image-generation requests or public submissions.

## Native Chart DIL compatibility

The primary input is native row-based DIL, for example:

`<Chart content={{"chartType":"bar","xKey":"x","series":[{"dataKey":"y","label":"Measured"}],"data":[{"x":"A","y":1.25},{"x":"B","y":4}]}} />`

Bar/line select literal row fields through xKey and explicit series dataKey.
Pie selects nameKey and either valueKey or a single series dataKey. If both pie
selectors exist they must agree. Missing series labels use their explicit source
field name, not a synthetic series or inferred numeric column. Unknown keys,
missing/null values, boolean numeric data, conflicting selectors and duplicate
series keys/labels are rejected. Source data are never interpolated, aggregated,
rounded, coerced from strings or filled with invented zeros.

The parser also accepts the previously tested explicit type/labels/series schema
and Chart.js-style literal data.labels/data.datasets. Native DIL is separately
covered by direct double-outer-brace fixtures for bar, line and pie. Numeric line
x values retain their actual nonuniform spacing and must strictly increase.

Supported optional fields are title, xAxisLabel, axisLabel, valuePrefix and
valueSuffix. Bounds support a literal min/max object, or unambiguous min/max or
yMin/yMax fields. Bounds that would hide source values are rejected. Other bound
schemas, stacking, scatter, aggregation, multiple pie series and arbitrary chart
libraries are unsupported. Bare JS object keys, bindings, function calls, spreads,
templates and executable expressions are not evaluated: the content must be
literal JSON inside the JSX wrapper, or an HTML-escaped quoted JSON attribute.

Input is bounded to 32,000 characters, 24 categories, six series and finite numeric
magnitudes at most 1e12. Duplicate JSON keys are rejected. Oversized, malformed,
unsupported and interrupted content uses a readable fallback rather than a fake
chart. Code examples remain literal; quoted backticks and markup inside valid
chart labels remain data.

## Supported BB rendering path

The adapter emits a normalized `chatgpt-rich` code fence. The plugin registers the
BB 0.48 `experimental_codeFenceRenderer` slot for that unique language. It never
claims the shared `ui`, JavaScript, Python or ordinary Markdown languages.

A closed valid fence renders actual responsive SVG/React bar, line or pie shapes.
An SVG title/description and a keyboard-operable exact-data table preserve the
source values and full labels. Long/dense axis ticks may be abbreviated or sampled;
the table retains every category and value. Prefix/suffix formatting affects visual
labels/tooltips; table numbers remain the raw selected numeric values. All-zero
bar data and zero pie entries remain in the table; pie requires a positive total.
Incomplete, interrupted or invalid fences render BB's Original code block. The
frontend independently validates the bounded normalized schema and duplicate keys.

The adapter must be paired with a plugin revision supporting `chatgpt-rich` before
integration. An older/disabled renderer shows source code, not a claimed native
visual. No new platform API or host DOM interception is required.

## Galleries and static controls

Literal image groups preserve source order, captions and explicit valid source
links in a responsive grid. Only existing src URLs or already-verified cached
asset references are used. Unknown query/ref entries remain explicit unavailable
cards; the grid never fabricates a matching image. Source links use BB's UrlLink.
Known images use BB's experimental_ImageLightbox with bounded previous/next
navigation. The existing two real metric/badge captures are used only as fixtures.
No screenshot image was synthesized, regenerated or replaced with stock content.

Generated gallery fences are protected from later markup normalization. Asset
append deduplication recognizes validated gallery payloads so already-rendered
cached images are not appended twice. Invalid galleries cannot suppress assets.

Literal button and checkbox values render static previews. Buttons are disabled;
checkboxes expose their checked and disabled accessibility state. A visible note
states that actions require ChatGPT Web. Dynamic checked-state expressions remain
fallbacks. There are no submit handlers, backend actions or inferred control state.
Styles target plugin-owned classes and BB tokens, not global BB typography or CSS.

## Verification

Latest checks: 283 Python tests passed; all 12 Node recipe tests passed; shared
renderer checks cover 11 normalized cases, four native cases, hostile payloads,
numeric line coordinates and streaming/interruption Original fallback. Plugin
build, TypeScript check and UI static preflight passed.

Local component screenshots and real BB message screenshots passed at 520 and
1500 CSS px, DPR 1, light/dark. Browser assertions check actual SVG shapes, exact
table values, numeric-x positions, document overflow, keyboard data disclosure,
caption/unavailable rendering and disabled/checked controls. The live gallery
opened the native BB lightbox, advanced to the second actual capture and dismissed
with Escape in each combination. Chart screenshots capture the SVG region after
source images settle and normal browser scrolling releases timeline follow mode;
no host CSS or DOM is hidden to manufacture screenshots.

The one hidden acceptance thread is `thr_dygpsk6tif`, backed by the explicit offline
QA provider. Its response is a committed fixed fixture generated through the actual
adapter formatter from native Chart DIL. It is a genuine BB timeline message, not
a ChatGPT model run or native ChatGPT screenshot. No duplicate test thread or agent
session was created. Local component fixtures stub the host lightbox and clearly
label that limitation; live BB acceptance uses the actual host component.

The QA companion plugin is separate from the production rich plugin. After
acceptance it is disabled to avoid competing for the language when the reviewed
production plugin is installed. Re-enable that same companion to inspect the same
recorded acceptance message; no prompt or new thread is required.

## Reproduce without model requests

From bb-plugin-rich-ui: install locked dependencies and run npm test, then npm run
qa:build. Run its existing TypeScript check. `qa/make-message.py` generates the
fixed acceptance payload from `qa/native-chart-cases.json` through the adapter.
`qa/review-rich.py --chromium "$CHROMIUM" --out "$EVIDENCE"` runs local fixtures.
Add `--thread "$EXISTING_ACCEPTANCE_THREAD"` for the recorded real BB message.
Both modes use ephemeral browser themes and block non-read HTTP methods. Local
image proxying is limited to the validated BB cli-image route; no arbitrary path
or remote fetch proxy is exposed.

The private `qa/host-plugin` uses only the supported SDK provider bridge and
code-fence slot. It emits fixed data and has no AI engine, command execution,
authentication, external service or tools. It is not a production provider.
If reconstructing it in a new checkout, its dependencies must resolve to the
parent plugin installation; build/install that private companion, enable it, and
reuse the recorded test thread. Do not claim both production and QA registrations
for chatgpt-rich at the same time: BB deliberately falls back on a conflict.

## Unsupported and remaining gates

- ChatGPT server-bound forms, input bindings, actions, maps, chart queries, image
  search/aliases, video hydration/playback and interactive generated apps remain
  unsupported or explicit fallbacks. This candidate does not execute generated JS.
- Exact host message typography, message toolbar and tool grouping remain owned
  by stock BB. The separately reviewed host patch is not changed or deployed here.
- Native chart styling, carousel geometry and theme-specific exact colors are not
  independently matched. These are supported native-looking alternatives.
- Same-message native ChatGPT captures, streaming/interruption visual comparisons
  and independent exact-parity acceptance are still missing. Local and actual BB
  screenshots are explicitly distinguished from native ChatGPT evidence.
- Main's newer image-metadata work at 927dee4 needs a separate integration review.
  No rebase, overwrite, force push or main publication was attempted.
