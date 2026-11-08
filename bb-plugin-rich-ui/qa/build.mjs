import { copyFile } from "node:fs/promises";
import { build } from "esbuild";
await build({ entryPoints: ["qa/fixture.tsx"], bundle: true, outfile: "qa/dist/fixture.js", platform: "browser", format: "esm", jsx: "automatic" });

await copyFile("dist/app.css", "qa/dist/plugin.css");

await build({entryPoints:["qa/rich-fixture.tsx"],bundle:true,outfile:"qa/dist/rich-fixture.js",platform:"browser",format:"esm",jsx:"automatic"});
