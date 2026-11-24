import type { AppHealthStatus as SummaryStatus, AppSummary, AppSummaryKind as RuntimeKind } from "@/lib/api";

type Series = (number | null)[];

const STATUS_RANK: Record<SummaryStatus, number> = {
  down: 0,
  degraded: 1,
  unknown: 2,
  healthy: 3,
};

export function needsAttention(status: SummaryStatus): boolean {
  return status === "down" || status === "degraded";
}

/** Down first, then degraded, unknown and healthy; by name inside each group. */
export function sortByAttention<T extends { name: string; status: SummaryStatus }>(rows: T[]): T[] {
  return [...rows].sort(
    (a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status] || a.name.localeCompare(b.name),
  );
}

export function attentionCount(rows: { status: SummaryStatus }[]): number {
  return rows.filter((r) => needsAttention(r.status)).length;
}

export type KindLabel = "service" | "frontend" | "datastore" | "worker";

const FRONTEND_HINT = /\b(front|frontend|web|site|static|landing|ui)\b/;

/** The chip shown next to an app name: a web app whose name or template says front end reads "Frontend". */
export function kindLabel(kind: RuntimeKind, ...hints: (string | null | undefined)[]): KindLabel {
  if (kind === "datastore") return "datastore";
  if (kind === "worker") return "worker";
  const text = hints.filter(Boolean).join(" ").toLowerCase().replace(/[-_]/g, " ");
  return FRONTEND_HINT.test(text) ? "frontend" : "service";
}

export type KindFilter = "all" | RuntimeKind;

export function matchesKind(kind: RuntimeKind, filter: KindFilter): boolean {
  return filter === "all" || filter === kind;
}

/** Zips parallel arrays into chart points; a missing or short series reads as gaps. */
export function toPoints(
  t: number[],
  series: Record<string, Series | undefined>,
): { t: number; [key: string]: number | null }[] {
  return t.map((at, i) => {
    const point: { t: number; [key: string]: number | null } = { t: at };
    for (const [key, values] of Object.entries(series)) point[key] = values?.[i] ?? null;
    return point;
  });
}

/** The highest point across several series, to name the app behind a spike. */
export function peakOf(
  t: number[],
  byKey: Record<string, Series>,
): { key: string; value: number; t: number } | null {
  let best: { key: string; value: number; t: number } | null = null;
  for (const [key, values] of Object.entries(byKey)) {
    for (let i = 0; i < values.length && i < t.length; i++) {
      const value = values[i];
      if (value != null && value > 0 && (best === null || value > best.value)) {
        best = { key, value, t: t[i] };
      }
    }
  }
  return best;
}

export interface DurationUnits {
  s: string;
  m: string;
  h: string;
  d: string;
}

/** "42s", "1m 42s", "39h", "3d": two units at most, the reader wants an order of magnitude. */
export function compactDuration(seconds: number, units: DurationUnits): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}${units.s}`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return s % 60 ? `${minutes}${units.m} ${s % 60}${units.s}` : `${minutes}${units.m}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}${units.h}`;
  return `${Math.floor(hours / 24)}${units.d}`;
}

export type SummaryDeploy = AppSummary["deploys"][number];

export function liveDeploy(deploys: SummaryDeploy[]): SummaryDeploy | null {
  return deploys.find((d) => d.outcome === "live") ?? null;
}

/**
 * The version prod would go back to: the newest replaced deploy older than the
 * live one, when the release history can still roll back to it.
 */
export function rollbackTarget(
  deploys: SummaryDeploy[],
  candidates: { version: string; promotionId: number | null }[],
): { version: string; promotionId: number } | null {
  const live = deploys.findIndex((d) => d.outcome === "live");
  if (live === -1) return null;
  const previous = deploys.slice(live + 1).find((d) => d.outcome === "replaced");
  if (!previous) return null;
  const match = candidates.find((c) => c.version === previous.version && c.promotionId != null);
  return match ? { version: previous.version, promotionId: match.promotionId as number } : null;
}
