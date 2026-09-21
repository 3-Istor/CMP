import type { FinopsResource } from "@/types";

/** Charte couleur des ressources (alignée sur le backend pricing.py). */
export const RESOURCE_META: Record<
  FinopsResource,
  { label: string; color: string }
> = {
  cpu: { label: "CPU", color: "#3B82F6" },
  ram: { label: "RAM", color: "#10B981" },
  storage: { label: "Stockage", color: "#F59E0B" },
  network: { label: "Réseau", color: "#8B5CF6" },
};

export const RESOURCES: FinopsResource[] = ["cpu", "ram", "storage", "network"];

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
