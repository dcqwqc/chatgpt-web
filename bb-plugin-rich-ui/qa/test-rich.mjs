import { build } from "esbuild";
import { readFile, rm } from "node:fs/promises";
import assert from "node:assert/strict";
await build({
  entryPoints: ["qa/rich-test-entry.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  external: ["react", "react-dom/server", "react/jsx-runtime", "@get-bb/plugin-sdk/app"],
  outfile: "qa/dist/test-rich.mjs",
});
const { parseRich, safeSource, ChartView, RichContent, createElement, renderToStaticMarkup } =
  await import("./dist/test-rich.mjs");
const cases = JSON.parse(await readFile("qa/rich-cases.json", "utf8"));
for (const c of cases) assert.equal(parseRich(JSON.stringify(c.value)) !== null, c.valid, c.name);
for (const bad of [
  '{"kind":"chart","kind":"control"}',
  '{"kind":"chart","labels":NaN}',
  "run()",
  "[]",
  "null",
  '{"kind":"gallery","images":[{"src":"file:///etc/passwd","alt":"Private"}]}',
])
  assert.equal(parseRich(bad), null);
assert.equal(safeSource("https://example.com/image.png"), true);
assert.equal(safeSource("http://localhost/private"), false);
assert.equal(safeSource("javascript:alert(1)"), false);
const native = JSON.parse(await readFile("qa/native-chart-cases.json", "utf8"));
for (const c of native) assert.deepEqual(parseRich(JSON.stringify(c.expected)), c.expected, c.name);
const html = renderToStaticMarkup(createElement(ChartView, { chart: native[1].expected }));
assert.match(html, /cx="55"/);
assert.match(html, /cx="180"/);
assert.match(html, /cx="555"/);
assert.match(html, /-2/);
assert.match(html, /3.5/);
for (const status of ["streaming", "unclosed"]) {
  const fallback = renderToStaticMarkup(
    createElement(RichContent, {
      code: JSON.stringify(native[0].expected),
      status,
      Original: () => createElement("pre", null, "Original"),
    }),
  );
  assert.equal(fallback, "<pre>Original</pre>");
}
await rm("qa/dist/test-rich.mjs");
console.log(`${cases.length} shared chart cases and hostile payload/URL checks passed`);
