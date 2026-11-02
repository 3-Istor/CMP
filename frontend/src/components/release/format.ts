import type { IntlLocale, Translate } from "@/components/finops/format";
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

export const PHASE_TONE: Record<ReleasePhase, keyof typeof TONE> = {
  Progressing: "info",
  Paused: "warn",
  Healthy: "ok",
  Degraded: "bad",
  Aborted: "bad",
};

export const OUTCOME_TONE: Record<ReleaseOutcome, keyof typeof TONE> = {
  running: "info",
  promoted: "ok",
  aborted_auto: "bad",
  aborted_manual: "bad",
  superseded: "muted",
};

export function isVisibleStep(step: { kind: ReleaseStepKind }): boolean {
  return step.kind !== "scale";
}

const ACTION_KEY: Record<string, string> = {
  promote: "continued",
  next: "continued",
  promote_next: "continued",
  full: "promotedAll",
  promote_full: "promotedAll",
  promoteFull: "promotedAll",
  skip: "skippedStep",
  abort: "aborted",
};

export function actionLabel(action: string, t: Translate): string {
  const key = ACTION_KEY[action];
  return key ? t(`action.${key}`) : action;
}

export function outcomeLabel(outcome: string, t: Translate): string {
  return outcome in OUTCOME_TONE ? t(`outcome.${outcome}`) : outcome;
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

export function formatDuration(
  seconds: number | null | undefined,
  t: Translate,
): string {
  if (seconds == null || seconds < 0) return "";
  if (seconds < 60) return t("duration.seconds", { count: Math.round(seconds) });
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) {
    return rest
      ? t("duration.minutesSeconds", { minutes, seconds: rest })
      : t("duration.minutes", { count: minutes });
  }
  const hours = Math.floor(minutes / 60);
  return t("duration.hoursMinutes", { hours, minutes: minutes % 60 });
}

export function durationBetween(
  start: string,
  end: string | null,
  t: Translate,
): string {
  if (!end) return "";
  return formatDuration((Date.parse(end) - Date.parse(start)) / 1000, t);
}

export function formatMetricValue(
  value: number | null,
  unit: string | null,
  t: Translate,
  locale: IntlLocale,
): string {
  if (value == null) return "-";
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
  switch (unit) {
    case "ratio":
      return t("metric.percent", { value: number.format(value * 100) });
    case "percent":
    case "%":
      return t("metric.percent", { value: number.format(value) });
    case "ms":
      return t("metric.milliseconds", { value: number.format(Math.round(value)) });
    case "s":
    case "seconds":
      return formatDuration(value, t);
    case null:
    case "":
    case "count":
      return number.format(value);
    default:
      return t("metric.withUnit", { value: number.format(value), unit });
  }
}

export function metricTone(metric: ReleaseMetric): "ok" | "bad" | "pending" {
  if (metric.phase === "Successful") return "ok";
  if (metric.phase === "Failed" || metric.phase === "Error") return "bad";
  return "pending";
}

export function formatDateTime(iso: string, locale: IntlLocale): string {
  return new Intl.DateTimeFormat(locale, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatTime(iso: string, locale: IntlLocale): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
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
