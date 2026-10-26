import type { FinopsCloud, FinopsResource } from "@/types";

/** Charte couleur des ressources (alignée sur le backend pricing.py). */
export const RESOURCE_META: Record<
  FinopsResource,
  { label: string; color: string }
> = {
  cpu: { label: "CPU", color: "#3B82F6" },
  ram: { label: "RAM", color: "#10B981" },
  storage: { label: "Stockage", color: "#F59E0B" },
  network: { label: "Réseau", color: "#8B5CF6" },
  ai: { label: "IA", color: "#EC4899" },
};

export const RESOURCES: FinopsResource[] = [
  "cpu",
  "ram",
  "storage",
  "network",
  "ai",
];

/** Couleurs par cloud, définies dans globals.css. */
export const CLOUD_META: Record<FinopsCloud, { label: string; color: string }> =
  {
    onprem: { label: "On-prem", color: "var(--cloud-onprem)" },
    aws: { label: "AWS", color: "var(--cloud-aws)" },
    gcp: { label: "GCP", color: "var(--cloud-gcp)" },
  };

export const CLOUDS: FinopsCloud[] = ["onprem", "aws", "gcp"];

/** Ressources à afficher : "ai" n'apparaît que lorsqu'il y a du coût IA. */
export function visibleResources(hasAi: boolean): FinopsResource[] {
  return RESOURCES.filter((r) => r !== "ai" || hasAi);
}

export function formatAge(iso: string | null, now = Date.now()): string {
  if (!iso) return "jamais";
  const hours = Math.floor(
    (now - new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime()) / 3_600_000,
  );
  if (hours < 1) return "il y a moins d'1 h";
  if (hours < 48) return `il y a ${hours} h`;
  return `il y a ${Math.floor(hours / 24)} j`;
}

/** Charte couleur budgétaire de la spec. */
export const STATUS_COLOR = {
  ok: "#10B981",
  warning: "#F59E0B",
  critical: "#EF4444",
} as const;

export function formatEUR(value: number, currency = "EUR"): string {
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value ?? 0);
}

export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
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
