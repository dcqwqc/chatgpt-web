import "./app.css";
import { definePluginApp } from "@get-bb/plugin-sdk/app";
import type { PluginMessageDirectiveProps } from "@get-bb/plugin-sdk/app";

/** Values come from provider message text; treat them as untrusted. */
export function parsePercent(value: string | undefined): number | null {
  if (typeof value !== "string" || !/^(?:100|\d{1,2})$/.test(value)) return null;
  const percent = Number(value);
  return Number.isFinite(percent) && percent >= 0 && percent <= 100 ? percent : null;
}

export function ChatGPTProgress({ attributes, source }: PluginMessageDirectiveProps) {
  const percent = parsePercent(attributes.value);
  if (percent === null) return <span className="text-muted-foreground">{source}</span>;
  const label = (attributes.label ?? "Progress").slice(0, 100);
  const completed = Math.max(0, Math.min(100, percent));
  const tone = attributes.tone;
  const fill = tone === "success" ? "var(--success)" :
               tone === "warning" ? "var(--warning)" :
               tone === "danger" ? "var(--destructive)" : "var(--primary)";
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={completed}
      aria-valuemin={0}
      aria-valuemax={100}
      className="my-2 w-full max-w-full overflow-hidden rounded-full bg-muted h-2"
    >
      <div
        className="h-full rounded-full transition-[width] duration-200 ease-out motion-reduce:transition-none"
        style={{ width: `${completed}%`, backgroundColor: fill }}
      />
    </div>
  );
}

export function ChatGPTBadge({ attributes, source }: PluginMessageDirectiveProps) {
  const label = attributes.label?.slice(0, 100) ?? "";
  if (!label.trim()) return <span className="text-muted-foreground">{source}</span>;
  const tone = attributes.tone;
  const color = tone === "success" ? "var(--success-foreground)" :
                tone === "danger" ? "var(--destructive)" :
                tone === "warning" ? "var(--foreground)" :
                tone === "info" ? "var(--color-blue-700)" : "var(--muted-foreground)";
  const tint = tone === "success" ? "var(--success)" :
               tone === "danger" ? "var(--destructive)" :
               tone === "warning" ? "var(--warning)" :
               tone === "info" ? "var(--color-blue-500)" : "var(--muted)";
  return (
    <span
      data-tone={tone}
      className="chatgpt-status-badge inline-flex max-w-full break-words items-center rounded-full px-2.5 py-0.5 text-xs font-medium leading-5"
      style={{ color: `var(--chatgpt-badge-text, ${color})`, overflowWrap: "anywhere", backgroundColor: `color-mix(in oklch, ${tint} 13%, transparent)` }}
    >
      {label}
    </span>
  );
}

/** A source metric row keeps its label/value association at narrow widths. */
export function ChatGPTMetric({ attributes, source }: PluginMessageDirectiveProps) {
  const label = attributes.label?.slice(0, 160) ?? "";
  const value = attributes.value?.slice(0, 80) ?? "";
  if (!label.trim() || !value.trim()) return <span>{source}</span>;
  return (
    <div className="my-2 grid max-w-full items-baseline gap-x-4 gap-y-1"
      style={{ gridTemplateColumns: "minmax(0, 1fr) auto" }}>
      <span className="min-w-0 text-sm text-muted-foreground" style={{ overflowWrap: "anywhere" }}>{label}</span>
      <strong className="text-right font-semibold" style={{ maxWidth: "12em", overflowWrap: "anywhere" }}>{value}</strong>
    </div>
  );
}

export default definePluginApp((app) => {
  app.slots.messageDirective({ id: "chatgpt-progress", component: ChatGPTProgress });
  app.slots.messageDirective({ id: "chatgpt-badge", component: ChatGPTBadge });
  app.slots.messageDirective({ id: "chatgpt-metric", component: ChatGPTMetric });
});
