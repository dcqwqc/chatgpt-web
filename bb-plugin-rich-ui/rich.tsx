import { useState } from "react";
import { experimental_ImageLightbox as ImageLightbox, UrlLink } from "@get-bb/plugin-sdk/app";
import type { ExperimentalCodeFenceRendererProps } from "@get-bb/plugin-sdk/app";

export type Chart = {
  kind: "chart";
  type: "bar" | "line" | "pie";
  title?: string;
  unit?: string;
  xAxisLabel?: string;
  axisLabel?: string;
  valuePrefix?: string;
  valueSuffix?: string;
  bounds?: { min?: number; max?: number };
  xValues?: number[];
  labels: string[];
  series: { name: string; values: number[] }[];
};
type Gallery = {
  kind: "gallery";
  caption?: string;
  images: (
    | { src: string; alt: string; caption?: string; href?: string }
    | { unavailable: string }
  )[];
};
type Control = { kind: "control"; type: "button" | "checkbox"; label: string; checked?: boolean };
export type Rich = Chart | Gallery | Control;
const plain = (v: unknown, limit: number): v is string =>
  typeof v === "string" && v.trim().length > 0 && v.length <= limit && !/[\x00-\x1f]/.test(v);
const object = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const keys = (v: Record<string, unknown>, allowed: string[]) =>
  Object.keys(v).every((k) => allowed.includes(k));
export function safeSource(v: unknown): v is string {
  if (typeof v !== "string" || v.length > 4000 || /[\s<>`\\]/.test(v)) return false;
  if (
    /^\/api\/v1\/plugins\/image-generation\/http\/(?:cli-image\?asset=file_[A-Za-z0-9]{12,80}|worker-image\?asset=[a-f0-9]{32})$/.test(
      v,
    )
  )
    return true;
  try {
    const u = new URL(v);
    return u.protocol === "https:" && !!u.hostname && !u.username && !u.password;
  } catch {
    return false;
  }
}
function uniqueJsonKeys(code: string) {
  const scopes: Set<string>[] = [];
  for (let i = 0; i < code.length; i++) {
    if (code[i] === "{") scopes.push(new Set());
    else if (code[i] === "}") scopes.pop();
    else if (code[i] === '"') {
      const start = i++;
      while (i < code.length && code[i] !== '"') {
        if (code[i] === "\\") i++;
        i++;
      }
      const token = code.slice(start, i + 1);
      let next = i + 1;
      while (/\s/.test(code[next] ?? "") && next < code.length) next++;
      if (code[next] === ":" && scopes.length) {
        const key = JSON.parse(token) as string;
        if (scopes[scopes.length - 1].has(key)) return false;
        scopes[scopes.length - 1].add(key);
      }
    }
  }
  return true;
}
export function parseRich(code: string): Rich | null {
  if (code.length > 32000) return null;
  let v: unknown;
  try {
    if (!uniqueJsonKeys(code)) return null;
    v = JSON.parse(code);
  } catch {
    return null;
  }
  if (!object(v)) return null;
  if (v.kind === "chart") {
    if (
      !keys(v, [
        "kind",
        "type",
        "title",
        "unit",
        "labels",
        "series",
        "xAxisLabel",
        "axisLabel",
        "valuePrefix",
        "valueSuffix",
        "bounds",
        "xValues",
      ]) ||
      !["bar", "line", "pie"].includes(String(v.type))
    )
      return null;
    if (
      (v.title !== undefined && !plain(v.title, 160)) ||
      (v.unit !== undefined && !plain(v.unit, 40))
    )
      return null;
    if (
      !Array.isArray(v.labels) ||
      !v.labels.length ||
      v.labels.length > 24 ||
      !v.labels.every((x) => plain(x, 80))
    )
      return null;
    if (!Array.isArray(v.series) || !v.series.length || v.series.length > 6) return null;
    const labels = v.labels;
    if (
      !v.series.every(
        (s) =>
          object(s) &&
          keys(s, ["name", "values"]) &&
          plain(s.name, 80) &&
          Array.isArray(s.values) &&
          s.values.length === labels.length &&
          s.values.every((n) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= 1e12),
      )
    )
      return null;
    const series = v.series as Chart["series"];
    if (
      v.type === "pie" &&
      (series.length !== 1 ||
        series[0].values.some((n) => n < 0) ||
        series[0].values.reduce((a, b) => a + b, 0) <= 0)
    )
      return null;
    const metadata: Partial<Chart> = {};
    for (const key of ["xAxisLabel", "axisLabel", "valuePrefix", "valueSuffix"] as const) {
      if (v[key] !== undefined) {
        if (typeof v[key] !== "string" || v[key].length > 160 || /[\x00-\x1f]/.test(v[key]))
          return null;
        metadata[key] = v[key];
      }
    }
    if (v.xValues !== undefined) {
      if (
        v.type !== "line" ||
        !Array.isArray(v.xValues) ||
        v.xValues.length !== labels.length ||
        !v.xValues.every(
          (n) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= 1e12,
        ) ||
        v.xValues.some((n, i, a) => i > 0 && a[i - 1] >= n)
      )
        return null;
      metadata.xValues = v.xValues;
    }
    if (v.bounds !== undefined) {
      const b = v.bounds;
      if (
        v.type === "pie" ||
        !object(b) ||
        !keys(b, ["min", "max"]) ||
        !Object.keys(b).length ||
        !Object.values(b).every(
          (n) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= 1e12,
        )
      )
        return null;
      const min = b.min as number | undefined,
        max = b.max as number | undefined;
      const values = series.flatMap((s) => s.values);
      if (
        (min !== undefined && min > Math.min(...values)) ||
        (max !== undefined && max < Math.max(...values)) ||
        (min !== undefined && max !== undefined && min >= max)
      )
        return null;
      metadata.bounds = {
        ...(min !== undefined ? { min } : {}),
        ...(max !== undefined ? { max } : {}),
      };
    }
    return {
      kind: "chart",
      type: v.type as Chart["type"],
      labels: labels as string[],
      series,
      ...metadata,
      ...(typeof v.title === "string" ? { title: v.title } : {}),
      ...(typeof v.unit === "string" ? { unit: v.unit } : {}),
    };
  }
  if (v.kind === "gallery") {
    if (
      !keys(v, ["kind", "images", "caption"]) ||
      (v.caption !== undefined && !plain(v.caption, 160)) ||
      !Array.isArray(v.images) ||
      !v.images.length ||
      v.images.length > 12
    )
      return null;
    if (
      !v.images.every(
        (i) =>
          object(i) &&
          ((keys(i, ["unavailable"]) && plain(i.unavailable, 240)) ||
            (keys(i, ["src", "alt", "caption", "href"]) &&
              safeSource(i.src) &&
              plain(i.alt, 120) &&
              (i.caption === undefined || plain(i.caption, 160)) &&
              (i.href === undefined || safeSource(i.href)))),
      )
    )
      return null;
    return v as Gallery;
  }
  if (
    v.kind === "control" &&
    keys(v, ["kind", "type", "label", "checked"]) &&
    ["button", "checkbox"].includes(String(v.type)) &&
    plain(v.label, 160) &&
    (v.checked === undefined || typeof v.checked === "boolean")
  )
    return v as Control;
  return null;
}
const colors = [
  "var(--color-blue-500, var(--primary))",
  "var(--success)",
  "var(--warning)",
  "var(--color-violet-500, var(--primary))",
  "var(--destructive)",
  "var(--muted-foreground)",
];
const short = (s: string) => (s.length > 14 ? s.slice(0, 12) + "…" : s);
export function ChartView({ chart: c }: { chart: Chart }) {
  const all = c.series.flatMap((s) => s.values);
  const low = c.bounds?.min ?? Math.min(0, ...all),
    high = c.bounds?.max ?? Math.max(0, ...all);
  const span = high - low || 1;
  const y = (n: number) => 200 - ((n - low) / span) * 170;
  const group = 500 / c.labels.length;
  const x = (i: number) =>
    c.xValues && c.xValues.length > 1
      ? 55 +
        ((c.xValues[i] - c.xValues[0]) / (c.xValues[c.xValues.length - 1] - c.xValues[0])) * 500
      : 55 + (i + 0.5) * group;
  const format = (n: number) => `${c.valuePrefix ?? ""}${n}${c.valueSuffix ?? ""}`;
  const baseline = Math.max(low, Math.min(high, 0));
  let angle = -Math.PI / 2;
  const total = c.series[0].values.reduce((a, b) => a + b, 0);
  const title = c.title ?? `${c.type} chart`;
  return (
    <figure className="chatgpt-chart" aria-label={title}>
      <figcaption>
        {title}
        {c.unit ? ` (${c.unit})` : ""}
      </figcaption>
      <svg
        viewBox="0 0 600 260"
        role="img"
        aria-label={`${title}. Exact values follow in the data table.`}
      >
        <title>{title}</title>
        <desc>{`${c.labels.length} categories, ${c.series.length} series. Data table contains all source values.`}</desc>
        {c.type !== "pie" && (
          <>
            <line
              x1="55"
              x2="555"
              y1={y(baseline)}
              y2={y(baseline)}
              stroke="var(--muted-foreground)"
            />
            <text x="48" y="28" textAnchor="end">
              {format(high)}
            </text>
            <text x="48" y="204" textAnchor="end">
              {format(low)}
            </text>
          </>
        )}
        {c.type === "bar" &&
          c.series.flatMap((s, j) =>
            s.values.map((v, i) => (
              <rect
                key={`${j}-${i}`}
                x={55 + i * group + (j * group * 0.8) / c.series.length + group * 0.1}
                y={Math.min(y(v), y(baseline))}
                width={(group * 0.8) / c.series.length}
                height={Math.abs(y(v) - y(baseline))}
                fill={colors[j]}
              >
                <title>{`${c.labels[i]} · ${s.name}: ${format(v)}${c.unit ?? ""}`}</title>
              </rect>
            )),
          )}
        {c.type === "line" &&
          c.series.map((s, j) => (
            <g key={j} fill={colors[j]}>
              <polyline
                points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
                fill="none"
                stroke={colors[j]}
                strokeWidth="2"
              />
              {s.values.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r="3">
                  <title>{`${c.labels[i]} · ${s.name}: ${format(v)}${c.unit ?? ""}`}</title>
                </circle>
              ))}
            </g>
          ))}
        {c.type === "pie" &&
          c.series[0].values.map((v, i) => {
            const start = angle;
            angle += (v / total) * Math.PI * 2;
            if (v === 0) return null;
            const path = `M 300 125 L ${300 + 95 * Math.cos(start)} ${125 + 95 * Math.sin(start)} A 95 95 0 ${v / total > 0.5 ? 1 : 0} 1 ${300 + 95 * Math.cos(angle)} ${125 + 95 * Math.sin(angle)} Z`;
            return v === total ? (
              <circle key={i} cx="300" cy="125" r="95" fill={colors[i % colors.length]}>
                <title>{`${c.labels[i]}: ${format(v)}`}</title>
              </circle>
            ) : (
              <path key={i} d={path} fill={colors[i % colors.length]}>
                <title>{`${c.labels[i]}: ${format(v)}`}</title>
              </path>
            );
          })}
        {c.type !== "pie" &&
          c.labels.map((label, i) =>
            i % Math.ceil(c.labels.length / 6) === 0 ? (
              <text key={i} x={x(i)} y="228" textAnchor="middle">
                <title>{label}</title>
                {short(label)}
              </text>
            ) : null,
          )}
        {c.type !== "pie" && c.xAxisLabel && (
          <text x="305" y="252" textAnchor="middle">
            {c.xAxisLabel}
          </text>
        )}
        {c.type !== "pie" && c.axisLabel && (
          <text transform="translate(12 115) rotate(-90)" textAnchor="middle">
            {c.axisLabel}
          </text>
        )}
      </svg>
      <div className="chatgpt-chart-legend">
        {(c.type === "pie" ? c.labels : c.series.map((s) => s.name)).map((name, i) => (
          <span key={i}>
            <i style={{ background: colors[i % colors.length] }} />
            {name}
          </span>
        ))}
      </div>
      <details>
        <summary>Exact chart data</summary>
        <div className="chatgpt-chart-data" role="region" aria-label={`${title} data`} tabIndex={0}>
          <table>
            <caption>
              {title}
              {c.unit ? ` · ${c.unit}` : ""}
            </caption>
            <thead>
              <tr>
                <th scope="col">Category</th>
                {c.series.map((s, i) => (
                  <th key={i} scope="col">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {c.labels.map((label, i) => (
                <tr key={i}>
                  <th scope="row">{label}</th>
                  {c.series.map((s, j) => (
                    <td key={j}>{String(s.values[i])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
export function GalleryView({ gallery: g }: { gallery: Gallery }) {
  const [selected, setSelected] = useState<number | null>(null);
  const available = g.images.flatMap((i, index) => ("src" in i ? [{ ...i, index }] : []));
  const current = selected === null ? undefined : available[selected];
  return (
    <section className="chatgpt-gallery" aria-label={g.caption ?? "Image group"}>
      {g.caption && <p className="chatgpt-gallery-caption">{g.caption}</p>}
      <div className="chatgpt-gallery-grid">
        {g.images.map((item, i) => (
          <figure key={i}>
            {"src" in item ? (
              <>
                <button
                  type="button"
                  className="chatgpt-gallery-open"
                  aria-label={`Open ${item.alt}`}
                  onClick={() => setSelected(available.findIndex((a) => a.index === i))}
                >
                  <img src={item.src} alt={item.alt} loading="lazy" />
                </button>
                {item.caption && <figcaption>{item.caption}</figcaption>}
                {item.href && <UrlLink href={item.href}>Image source</UrlLink>}
              </>
            ) : (
              <figcaption className="chatgpt-unavailable">{item.unavailable}</figcaption>
            )}
          </figure>
        ))}
      </div>
      <ImageLightbox
        imageSrc={current?.src ?? null}
        imageAlt={current?.alt ?? "Image"}
        title="Image group"
        isOpen={current !== undefined}
        hasMultipleImages={available.length > 1}
        previousDisabled={selected === 0}
        nextDisabled={selected === available.length - 1}
        navigationStatus={selected === null ? undefined : `${selected + 1} / ${available.length}`}
        onClose={() => setSelected(null)}
        onPrevious={() => setSelected((n) => Math.max(0, (n ?? 0) - 1))}
        onNext={() => setSelected((n) => Math.min(available.length - 1, (n ?? 0) + 1))}
      />
    </section>
  );
}
function StaticControl({ control: c }: { control: Control }) {
  return (
    <div className="chatgpt-static-control">
      {c.type === "button" ? (
        <button disabled type="button">
          {c.label}
        </button>
      ) : (
        <span role="checkbox" aria-checked={c.checked ?? false} aria-disabled="true">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <rect x="2" y="2" width="16" height="16" rx="3" />
            {c.checked && <path d="m5 10 3 3 7-7" />}
          </svg>
          {c.label}
        </span>
      )}
      <small>Static preview · actions require ChatGPT Web</small>
    </div>
  );
}
export function RichContent({ code, status, Original }: ExperimentalCodeFenceRendererProps) {
  const value = status === "closed" ? parseRich(code) : null;
  if (!value) return <Original />;
  return value.kind === "chart" ? (
    <ChartView chart={value} />
  ) : value.kind === "gallery" ? (
    <GalleryView gallery={value} />
  ) : (
    <StaticControl control={value} />
  );
}
