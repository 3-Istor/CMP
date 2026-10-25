import type {
  ReleaseMetric,
  ReleaseOutcome,
  ReleasePhase,
  ReleaseStepKind,
} from "@/types";

export const TONE = {
  ok: "border-green-600/40 bg-green-600/10 text-green-700 dark:text-green-400",
  warn: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  bad: "border-red-600/40 bg-red-600/15 text-red-700 dark:text-red-400",
  info: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  muted: "text-muted-foreground",
} as const;

export const TEXT_OK = "text-green-700 dark:text-green-400";
export const TEXT_BAD = "text-red-700 dark:text-red-400";

export const PHASE: Record<ReleasePhase, { label: string; tone: keyof typeof TONE }> = {
  Progressing: { label: "En cours", tone: "info" },
  Paused: { label: "En pause", tone: "warn" },
  Healthy: { label: "Stable", tone: "ok" },
  Degraded: { label: "Dégradée", tone: "bad" },
  Aborted: { label: "Abortée", tone: "bad" },
};

export const OUTCOME: Record<ReleaseOutcome, { label: string; tone: keyof typeof TONE }> = {
  running: { label: "En cours", tone: "info" },
  promoted: { label: "Promue", tone: "ok" },
  aborted_auto: { label: "Rejetée auto", tone: "bad" },
  aborted_manual: { label: "Abortée", tone: "bad" },
  superseded: { label: "Remplacée", tone: "muted" },
};

export const STRATEGY: Record<string, string> = {
  rolling: "Rolling",
  canary: "Canary",
  bluegreen: "Blue/green",
};

export const STEP_KIND: Record<ReleaseStepKind, string> = {
  analysis: "analyse",
  setWeight: "poids",
  pause: "pause",
  scale: "pods",
};

export function isVisibleStep(step: { kind: ReleaseStepKind }): boolean {
  return step.kind !== "scale";
}

const ACTION: Record<string, string> = {
  promote: "a continué la release",
  next: "a continué la release",
  promote_next: "a continué la release",
  full: "a tout promu",
  promote_full: "a tout promu",
  promoteFull: "a tout promu",
  skip: "a passé une étape",
  abort: "a aborté la release",
};

export function actionLabel(action: string): string {
  return ACTION[action] ?? action;
}

/** `ghcr.io/org/web:1.4.0` -> `1.4.0`, `...@sha256:abcdef…` -> `sha256:abcdef1`. */
export function imageTag(image: string | null | undefined): string {
  if (!image) return "-";
  const at = image.indexOf("@");
  if (at !== -1) return image.slice(at + 1, at + 15);
  const lastSlash = image.lastIndexOf("/");
  const colon = image.lastIndexOf(":");
  return colon > lastSlash ? image.slice(colon + 1) : image.slice(lastSlash + 1);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest ? `${minutes} min ${rest}` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60}`;
}

export function durationBetween(start: string, end: string | null): string {
  if (!end) return "";
  return formatDuration((Date.parse(end) - Date.parse(start)) / 1000);
}

const NUMBER = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

export function formatMetricValue(value: number | null, unit: string | null): string {
  if (value == null) return "-";
  switch (unit) {
    case "ratio":
      return `${NUMBER.format(value * 100)} %`;
    case "percent":
    case "%":
      return `${NUMBER.format(value)} %`;
    case "ms":
      return `${NUMBER.format(Math.round(value))} ms`;
    case "s":
    case "seconds":
      return formatDuration(value);
    case null:
    case "":
    case "count":
      return NUMBER.format(value);
    default:
      return `${NUMBER.format(value)} ${unit}`;
  }
}

export function metricTone(metric: ReleaseMetric): "ok" | "bad" | "pending" {
  if (metric.phase === "Successful") return "ok";
  if (metric.phase === "Failed" || metric.phase === "Error") return "bad";
  return "pending";
}

const DATE_TIME = new Intl.DateTimeFormat("fr-FR", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const TIME = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });

export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

export function errorMessage(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  try {
    const detail = JSON.parse(text).detail;
    return typeof detail === "string" ? detail : text;
  } catch {
    return text;
  }
}
