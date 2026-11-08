import React from "react";
import { createRoot } from "react-dom/client";
import { RichContent } from "../rich";
import cases from "./native-chart-cases.json";
import "../app.css";
const runtime = globalThis as typeof globalThis & {
  __bbPluginRuntime?: { pluginSdkApp: Record<string, unknown>; react: typeof React };
};
runtime.__bbPluginRuntime = {
  react: React,
  pluginSdkApp: {
    experimental_ImageLightbox: () => null,
    UrlLink: ({ href, children }: { href: string; children: React.ReactNode }) => (
      <a href={href}>{children}</a>
    ),
  },
};
const valid = cases.slice(0, 3);
const Original = () => <pre>Unfinished or unsupported data: original code retained</pre>;
createRoot(document.getElementById("root")!).render(
  <main className="rich-fixture">
    <p>Actual plugin components · local fixture · host lightbox stubbed here</p>
    {valid.map((c, i) => (
      <RichContent
        key={i}
        code={JSON.stringify(c.expected)}
        status="closed"
        index={i}
        language="chatgpt-rich"
        Original={Original}
        message={{ id: "fixture", threadId: "fixture", turnId: "fixture", projectId: null }}
      />
    ))}
    {[
      {
        kind: "gallery",
        caption: "Existing real BB captures",
        images: [
          {
            src: "/api/v1/plugins/image-generation/http/cli-image?asset=file_BBParityMetric20261108A",
            alt: "Metric",
            caption: "Metric screenshot",
          },
          {
            src: "/api/v1/plugins/image-generation/http/cli-image?asset=file_BBParityBadge20261108A",
            alt: "Badge",
            caption: "Badge screenshot",
          },
          { unavailable: "Image query unavailable — no asset data" },
        ],
      },
      { kind: "control", type: "button", label: "Save" },
      { kind: "control", type: "checkbox", label: "Literal checked state", checked: true },
    ].map((value, i) => (
      <RichContent
        key={i}
        code={JSON.stringify(value)}
        status="closed"
        index={i + 3}
        language="chatgpt-rich"
        Original={Original}
        message={{ id: "fixture", threadId: "fixture", turnId: "fixture", projectId: null }}
      />
    ))}
  </main>,
);
