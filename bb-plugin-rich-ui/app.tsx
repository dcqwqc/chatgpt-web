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
                tone === "warning" ? "var(--warning-foreground)" :
                tone === "info" ? "var(--color-blue-700)" : "var(--muted-foreground)";
  const tint = tone === "success" ? "var(--success)" :
               tone === "danger" ? "var(--destructive)" :
               tone === "warning" ? "var(--warning)" :
               tone === "info" ? "var(--color-blue-500)" : "var(--muted)";
  return (
    <span
      className="inline-flex max-w-full items-center rounded-full px-2.5 py-0.5 text-xs font-medium leading-5"
      style={{ color, backgroundColor: `color-mix(in oklch, ${tint} 13%, transparent)` }}
    >
      {label}
    </span>
  );
}

export default definePluginApp((app) => {
  app.slots.messageDirective({ id: "chatgpt-progress", component: ChatGPTProgress });
  app.slots.messageDirective({ id: "chatgpt-badge", component: ChatGPTBadge });
});
