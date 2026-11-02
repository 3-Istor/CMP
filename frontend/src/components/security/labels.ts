import type { SecurityCategory, SecurityTier } from "@/types";

export const TIER_META: Record<SecurityTier, { color: string; badge: string }> = {
  core: {
    color: "#dc2626",
    badge: "bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/30",
  },
  important: {
    color: "#f59e0b",
    badge:
      "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30",
  },
  recommended: {
    color: "#94a3b8",
    badge: "bg-muted text-muted-foreground border-border",
  },
  info: {
    color: "#cbd5e1",
    badge: "bg-muted text-muted-foreground border-border",
  },
};

export const OK_COLOR = "#16a34a";
export const EMPTY_COLOR = "#e2e8f0";

export type SecurityTab =
  | "overview"
  | "code"
  | "access"
  | "data"
  | "journal"
  | "ignored";

export const CATEGORY_TAB: Record<SecurityCategory, SecurityTab> = {
  leaks: "code",
  dependencies: "code",
  container: "code",
  access: "access",
  data: "data",
  journal: "journal",
};

export const TABS: { value: SecurityTab; categories: SecurityCategory[] }[] = [
  { value: "overview", categories: [] },
  { value: "code", categories: ["leaks", "dependencies", "container"] },
  { value: "access", categories: ["access"] },
  { value: "data", categories: ["data"] },
  { value: "journal", categories: ["journal"] },
  { value: "ignored", categories: [] },
];

export const JUSTIFICATIONS = [
  "component_not_present",
  "vulnerable_code_not_present",
  "vulnerable_code_not_in_execute_path",
  "vulnerable_code_cannot_be_controlled_by_adversary",
  "inline_mitigations_already_exist",
] as const;

const RELATIVE_UNIT_MS = 60_000;

/** "il y a 3 h", "dans 21 h": API datetimes are naive UTC. */
export function relativeTime(iso: string | null, locale: string): string | null {
  if (!iso) return null;
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  const minutes = Math.round((date.getTime() - Date.now()) / RELATIVE_UNIT_MS);
  if (Math.abs(minutes) < 60) return relative.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return relative.format(hours, "hour");
  return relative.format(Math.round(hours / 24), "day");
}

/** Backend errors come back as a JSON body with a ``detail`` field. */
export function errorMessage(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  try {
    const detail = JSON.parse(text).detail;
    return typeof detail === "string" ? detail : text;
  } catch {
    return text;
  }
}
