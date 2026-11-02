"use client";

import type { Budget } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import { budgetStatusColor, formatEUR, intlLocale } from "./format";

/**
 * Barre de progression budget vs consommé avec code couleur dynamique
 * selon les seuils du budget (vert sous l'attention, orange sous le critique,
 * rouge au-delà ; 70 % / 90 % par défaut).
 */
export function BudgetGauge({ budget }: { budget: Budget }) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const pct = budget.consumed_pct ?? 0;
  const clamped = Math.min(100, Math.max(0, pct));
  const color = budgetStatusColor(
    budget.consumed_pct,
    budget.threshold_warn,
    budget.threshold_critical,
  );

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-2xl font-semibold tabular-nums">
          {formatEUR(budget.spent_eur, budget.currency, locale)}
        </span>
        <span className="text-sm text-muted-foreground">
          / {formatEUR(budget.monthly_amount_eur, budget.currency, locale)}
        </span>
      </div>

      <div className="h-2.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${clamped}%`, backgroundColor: color }}
        />
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="font-medium tabular-nums" style={{ color }}>
          {budget.consumed_pct !== null
            ? t("budgetGauge.consumed", {
                percent: budget.consumed_pct.toFixed(0),
              })
            : t("common.na")}
        </span>
        <span className="text-muted-foreground">
          {t("budgetGauge.remaining", {
            amount: formatEUR(budget.remaining_eur, budget.currency, locale),
          })}
        </span>
      </div>
    </div>
  );
}
