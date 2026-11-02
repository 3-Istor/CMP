import type { FinopsCloud, FinopsResource } from "@/types";

export type IntlLocale = "fr-FR" | "en-GB";

export type Translate = (
  key: string,
  values?: Record<string, string | number>,
) => string;

/** Charte couleur des ressources (alignée sur le backend pricing.py). */
export const RESOURCE_META: Record<FinopsResource, { color: string }> = {
  cpu: { color: "#3B82F6" },
  ram: { color: "#10B981" },
  storage: { color: "#F59E0B" },
  network: { color: "#8B5CF6" },
  ai: { color: "#EC4899" },
  platform: { color: "#94A3B8" },
};

export const RESOURCES: FinopsResource[] = [
  "cpu",
  "ram",
  "storage",
  "network",
  "ai",
  "platform",
];

/** Couleurs par cloud, définies dans globals.css. */
export const CLOUD_META: Record<FinopsCloud, { label: string; color: string }> =
  {
    onprem: { label: "On-prem", color: "var(--cloud-onprem)" },
    aws: { label: "AWS", color: "var(--cloud-aws)" },
    gcp: { label: "GCP", color: "var(--cloud-gcp)" },
  };

export const CLOUDS: FinopsCloud[] = ["onprem", "aws", "gcp"];

/** "ai" et "platform" n'apparaissent que lorsqu'ils ont un coût. */
export function visibleResources(
  hasAi: boolean,
  hasPlatform: boolean,
): FinopsResource[] {
  return RESOURCES.filter(
    (r) => (r !== "ai" || hasAi) && (r !== "platform" || hasPlatform),
  );
}

/** next-intl locale ("fr" | "en") to the Intl locale used for money and numbers. */
export function intlLocale(locale: string): IntlLocale {
  return locale === "en" ? "en-GB" : "fr-FR";
}

export function formatAge(
  iso: string | null,
  t: Translate,
  now = Date.now(),
): string {
  if (!iso) return t("age.never");
  const hours = Math.floor(
    (now - new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime()) / 3_600_000,
  );
  if (hours < 1) return t("age.lessThanHour");
  if (hours < 48) return t("age.hoursAgo", { count: hours });
  return t("age.daysAgo", { count: Math.floor(hours / 24) });
}

/** Charte couleur budgétaire de la spec. */
export const STATUS_COLOR = {
  ok: "#10B981",
  warning: "#F59E0B",
  critical: "#EF4444",
} as const;

function roundToTwoSignificantDigits(value: number): number {
  return Number(value.toPrecision(2));
}

export function formatEUR(
  value: number,
  currency = "EUR",
  locale: IntlLocale = "fr-FR",
): string {
  const amount = value ?? 0;
  const magnitude = Math.abs(amount);
  const money = (v: number, min: number, max: number) =>
    new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: min,
      maximumFractionDigits: max,
    }).format(v);

  if (magnitude === 0) return money(0, 0, 0);
  if (magnitude < 0.01) {
    return amount > 0 ? `< ${money(0.01, 2, 2)}` : `> ${money(-0.01, 2, 2)}`;
  }
  if (magnitude < 1) return money(roundToTwoSignificantDigits(amount), 0, 3);
  return money(amount, 2, 2);
}

export function formatNumber(
  value: number,
  digits = 1,
  locale: IntlLocale = "fr-FR",
): string {
  const amount = value ?? 0;
  const magnitude = Math.abs(amount);
  const plain = (v: number, max: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: max }).format(v);

  if (magnitude === 0) return "0";
  if (magnitude < 0.01) {
    return amount > 0 ? `< ${plain(0.01, 2)}` : `> ${plain(-0.01, 2)}`;
  }
  if (magnitude < 1) return plain(roundToTwoSignificantDigits(amount), 3);
  return plain(amount, digits);
}

/** Décimales de l'axe choisies selon l'échelle : centimes sous 1, une décimale sous 10, entiers au-delà. */
export function axisTickFormatter(
  maxValue: number,
  suffix = "",
  locale: IntlLocale = "fr-FR",
) {
  const decimals =
    maxValue < 0.1 ? 3 : maxValue < 1 ? 2 : maxValue < 10 ? 1 : 0;
  const formatter = new Intl.NumberFormat(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return (value: number) => `${formatter.format(value)}${suffix}`;
}

export function formatPct(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** Renvoie la couleur d'un pourcentage de budget consommé selon les seuils. */
export function budgetStatusColor(
  consumedPct: number | null,
  warn = 70,
  critical = 90,
): string {
  if (consumedPct === null) return STATUS_COLOR.ok;
  if (consumedPct >= critical) return STATUS_COLOR.critical;
  if (consumedPct >= warn) return STATUS_COLOR.warning;
  return STATUS_COLOR.ok;
}

const PARIS_TIME_ZONE = "Europe/Paris";

export function parisClock(now: Date): { date: string; hour: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: PARIS_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    hour: Number(get("hour")) + Number(get("minute")) / 60,
  };
}
