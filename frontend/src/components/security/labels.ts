import type {
  SecurityCategory,
  SecurityException,
  SecurityScan,
  SecurityTier,
} from "@/types";

export const TIER_META: Record<
  SecurityTier,
  { label: string; color: string; badge: string }
> = {
  core: {
    label: "Socle",
    color: "#dc2626",
    badge: "bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/30",
  },
  important: {
    label: "Important",
    color: "#f59e0b",
    badge:
      "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30",
  },
  recommended: {
    label: "Recommandé",
    color: "#94a3b8",
    badge: "bg-muted text-muted-foreground border-border",
  },
  info: {
    label: "Info",
    color: "#cbd5e1",
    badge: "bg-muted text-muted-foreground border-border",
  },
};

export const OK_COLOR = "#16a34a";
export const EMPTY_COLOR = "#e2e8f0";

export const CATEGORY_META: Record<
  SecurityCategory,
  { label: string; question: string; tab: SecurityTab }
> = {
  leaks: {
    label: "Fuites",
    question: "Mon code fuit-il ?",
    tab: "code",
  },
  dependencies: {
    label: "Dépendances",
    question: "Mes dépendances ont-elles des failles ?",
    tab: "code",
  },
  container: {
    label: "Conteneur",
    question: "Mon conteneur est-il bien construit ?",
    tab: "code",
  },
  access: {
    label: "Accès",
    question: "Qui peut atteindre mon app ?",
    tab: "access",
  },
  data: {
    label: "Données",
    question: "Mes données sont-elles protégées ?",
    tab: "data",
  },
  journal: {
    label: "Journal",
    question: "Que s'est-il passé ?",
    tab: "journal",
  },
};

export type SecurityTab =
  | "overview"
  | "code"
  | "access"
  | "data"
  | "journal"
  | "ignored";

export const TABS: { value: SecurityTab; label: string; categories: SecurityCategory[] }[] = [
  { value: "overview", label: "Vue d'ensemble", categories: [] },
  {
    value: "code",
    label: "Code et dépendances",
    categories: ["leaks", "dependencies", "container"],
  },
  { value: "access", label: "Accès et exposition", categories: ["access"] },
  { value: "data", label: "Données", categories: ["data"] },
  { value: "journal", label: "Journal", categories: ["journal"] },
  { value: "ignored", label: "Ignorées", categories: [] },
];

export const SOURCE_LABELS: Record<SecurityScan["source"], string> = {
  "trivy-operator": "Images en cours · Trivy Operator",
  ci: "Code · GitHub Actions",
  kyverno: "Conteneurs · Kyverno",
  cnpg: "Sauvegardes · CNPG",
  exposure: "Exposition",
  cilium: "Isolation réseau",
};

export const EXCEPTION_STATUS_LABELS: Record<SecurityException["status"], string> = {
  not_affected: "Ne nous concerne pas",
  false_positive: "Faux positif",
  accepted_risk: "Risque accepté",
  revoked: "Secret révoqué",
};

export const JUSTIFICATION_LABELS: Record<string, string> = {
  component_not_present: "Le paquet vulnérable n'est pas dans l'image",
  vulnerable_code_not_present: "Le code vulnérable n'est pas présent",
  vulnerable_code_not_in_execute_path: "Le code vulnérable n'est jamais appelé",
  vulnerable_code_cannot_be_controlled_by_adversary:
    "Un attaquant ne peut pas atteindre ce code",
  inline_mitigations_already_exist:
    "Une protection en place bloque déjà l'attaque",
};

const RELATIVE = new Intl.RelativeTimeFormat("fr", { numeric: "auto" });

/** "il y a 3 h", "dans 21 h": API datetimes are naive UTC. */
export function relativeTime(iso: string | null): string {
  if (!iso) return "jamais";
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  const minutes = Math.round((date.getTime() - Date.now()) / 60000);
  if (Math.abs(minutes) < 60) return RELATIVE.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return RELATIVE.format(hours, "hour");
  return RELATIVE.format(Math.round(hours / 24), "day");
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
