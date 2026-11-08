import React from "react";
import { createRoot } from "react-dom/client";
import { ChatGPTBadge, ChatGPTMetric, ChatGPTProgress } from "../app";
import type { PluginMessageDirectiveProps } from "@get-bb/plugin-sdk/app";

const props = (attributes: Record<string, string>): PluginMessageDirectiveProps => ({
  attributes, source: "Invalid source fallback", message: {
    id: "local-fixture", threadId: "local-fixture", turnId: "local-fixture", projectId: "local-fixture",
  }, openWorkspaceFile: null,
});
const tones = ["success", "warning", "danger", "info", "neutral"];
createRoot(document.getElementById("root")!).render(
  <main className="mx-auto max-w-3xl p-6 text-foreground">
    <p className="text-sm text-muted-foreground">Isolated component fixture • actual plugin source • BB stylesheet</p>
    <h2 className="my-6 text-xl font-semibold">Results</h2>
    <section aria-label="Readiness timings">
      <ChatGPTMetric {...props({ label: "Before — browser readiness", value: "4.06 s" })}/>
      <ChatGPTProgress {...props({ value: "100", tone: "neutral" })}/>
      <ChatGPTMetric {...props({ label: "After — browser readiness", value: "2.82–2.97 s" })}/>
      <ChatGPTProgress {...props({ value: "70", tone: "success" })}/>
    </section>
    <h2 className="my-6 text-xl font-semibold">Status variants</h2>
    <div className="grid gap-3">{tones.map(tone => <div key={tone} data-tone={tone}>
      <ChatGPTBadge {...props({ label: `${tone}: Verified status`, tone })}/>
    </div>)}</div>
    <h2 className="my-6 text-xl font-semibold">Long content</h2>
    <section aria-label="Long content">
      <ChatGPTMetric {...props({ label: "VeryLongUnbrokenLabel".repeat(8), value: "VeryLongUnbrokenValue".repeat(4) })}/>
      <ChatGPTBadge {...props({ label: "UnbrokenStatusLabel".repeat(8), tone: "success" })}/>
    </section>
    <h2 className="my-6 text-xl font-semibold">Boundary values</h2>
    {["0", "100", "NaN"].map(value => <ChatGPTProgress key={value} {...props({ value })}/>)}
  </main>
);
