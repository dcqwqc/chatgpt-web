# BB ChatGPT image/media parity — isolated candidate

## Result

This candidate is based on the verified `ca347ff` image-URL fix. It preserves
explicit child images in AsyncImageGroup, resolves exact asset references only
against already-validated conversation cache files, preserves explicit image
source/citation URLs, and prevents appending a generated image twice when the
canonical answer already embeds it. There are no new image-generation calls,
backend endpoint guesses, arbitrary path fetches or upstream BB source changes.

It is implemented and unit tested in an isolated branch, **not deployed**.
The original conversation and shared daemons were not changed. Existing live BB
image behavior was verified separately; those captures do not prove the new
candidate is installed or that native ChatGPT parity is complete.

## Supported paths and source metadata

`normalize_message` in the CLI exports message identity, role, channel, parts and
text. It does not export arbitrary native message metadata. The relevant existing
normalized source-conversation cache was inspected read-only: it contains no
message metadata and no hydrated media files. No private message text was copied
into this report. Query/ref-only markup cannot be resolved from that cache.

The existing `generated_media.image_asset_ids` recognizes only explicit
`image_asset_pointer` parts from assistant/tool messages, with `file-service://`
or `sediment://` and validated file IDs. It excludes uploaded user images.
`download_generated_images` performs the existing authenticated lookup for those
pointers, validates signed-download hostnames, disables arbitrary redirects,
limits bytes, verifies image signatures/decoding and caches private files.
This phase audited that source path and its tests; it did not perform new remote
ChatGPT downloads or inspect authentication credentials.

There is no verified query-to-image endpoint or alias resolver in that bridge.
In particular, `query="a tree"` or `ref="turn0image0"` does not prove any asset
identity. Such values remain descriptive unavailable placeholders. No association
is inferred from query similarity, file order or filename guesses. Native metadata
schemas and a supported resolver/export contract are the next dependency.

For an exact `file_...`, `file-service://file_...` or `sediment://file_...`
reference, the adapter now consults its existing verified media list. That list
is confined to the fixed media cache, validates names, real paths, size and PNG
signature, and belongs to the current conversation. Only membership in that list
permits producing the existing BB-relative image route. No path from message text
is opened. Plain commentary avoids cache scans; replay checks its asset list once.
A missing/invalid explicit src never silently falls back to a different reference.

The current BB cli-image server supports PNG files only. The existing downloader
can save JPEG/WebP/GIF, but those cached formats still cannot flow through that
PNG route. Explicit HTTPS URLs can render whatever formats the browser supports;
this does not establish cache-route or animation parity. Worker-image uses a
32-character hexadecimal asset ID rather than a file_ ID; its URL validation now
matches the actual server contract.

## Groups, citations and native BB viewing

AsyncImageGroup containers are removed while preserving explicit child image
order and available sibling content. Previously the later container replacement
could discard images that had already been converted. Self-closing data-only
groups remain unavailable rather than evaluating expressions. A list of Markdown
images is not the same as ChatGPT's native carousel, grid, cropping or attribution.

Explicit image href attributes become separate source links, preserving the
image click for BB's own lightbox. Explicit Cite url/href attributes become
bounded source links. Reference-only Cite, FileCite and MemoryCite remain
unresolved; there is no invented link to a private file or memory resource.
HTTPS destinations and the known relative image routes are validated. Malformed
ports fail closed. No source links are fetched server-side by this formatter.

Stock BB 0.48.0 already renders Markdown images with MarkdownRenderedImage,
InlineImageGalleryContext and ImageLightbox. The SDK also exports
experimental_ImageLightbox, including image/video mode and navigation props.
Using ordinary Markdown keeps the core viewer and avoids adding another plugin
renderer. Its existing source tests cover dismissal/navigation; this phase also
verified real live single-image open and Escape dismissal in isolated Chromium.
Multi-image lightbox navigation for the new gallery output is not live verified.

When a canonical reply already embeds a hydrated image, the adapter now excludes
that asset from its later generated-media append. Literal Markdown examples and
plain source hyperlinks do not suppress a real generated image. The adapter
regression exercises both image-only replies and explicit asset-reference replies
without making a model request.

## Real evidence and reproduction

`qa/media_host_capture.py` opens an **existing BB test thread** in an isolated
headless browser. All non-read HTTP methods are blocked. It captures 520/1500 CSS
px, DPR 1, light/dark, using only a temporary document theme class. Optional
`--verify-lightbox` opens the core viewer and verifies Escape dismissal in that
private browser; it never sends a prompt or changes persisted preferences.

With `--reference-dir`, IDs are validated and files must resolve directly under
the explicitly supplied reference directory. The script compares endpoint-served
bytes to those saved originals by SHA-256. It never treats an arbitrary path in
an assistant message as permission to read a file.

Run with installed Python Playwright and a compatible Chromium executable:
`python3 qa/media_host_capture.py --chromium "$CHROMIUM" --thread "$EXISTING_THREAD"
--reference-dir "$APPROVED_IMAGE_CACHE" --verify-lightbox --out "$EVIDENCE"`.
The default host is loopback BB; override `--host` when appropriate.

The specified existing test thread currently displays a previously generated
tree image, not the metric/badge screenshots. Actual screenshots of that BB
message and viewer were captured in both themes and widths, and served bytes
matched its saved image in every combination. The metric and badge PNG files
were separately inspected as genuine existing BB screen captures. They were not
generated, edited, represented with a search query, or relabelled as native ChatGPT.
No new screenshot/image was synthesized. Screenshots of the existing host and
source asset bytes are distinct evidence categories.

The full Python suite passes 268 tests; the Node recipe suite passes 12 tests.
Source/runtime URL tests use fixtures. New gallery/reference behavior remains
undeployed; existing live image/lightbox checks do not replace its integration gate.

## Exact unimplemented parity and dependencies

- Native query search, image aliases, citation metadata hydration and native
  image attribution require a verified source export/resolver contract.
- Gallery grid/carousel geometry, zoom/cropping defaults, multiple-image ordering
  across messages, loading states and matched native tool/image layouts are not
  independently accepted.
- Cached non-PNG delivery, native video asset hydration and playback are not
  implemented here. BB's MarkdownVideo and SDK video viewer are available, but
  the adapter still uses an AsyncVideo fallback; no verified video fixture or
  paired native playback evidence was supplied or fabricated.
- Native interactive apps, controls, maps, chart-data hydration and executable
  generated components remain fallbacks. No remote code or JSX binding is run.
- No paired native ChatGPT image/message screenshot was captured. The authorized
  isolated graphical route was already reported unavailable in the platform
  phase; desktop profiles/cookies and arbitrary CDP endpoints were not used.

Before integration: review this candidate, then verify its exact adapter revision
with an isolated replay containing explicit groups and verified references. Do not
restart a shared daemon or resend an accepted prompt to obtain that evidence.
Exact parity further requires authorized matched native captures of the same
message, assets, viewport, zoom, fonts, theme and streaming/interruption states,
plus independent comparison. The Loom task remains incomplete.
