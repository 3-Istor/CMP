"use client";

import type { Budget } from "@/types";
import { budgetStatusColor, formatEUR } from "./format";

/**
 * Barre de progression budget vs consommé avec code couleur dynamique
 * (🟢 <70% · 🟡 70-90% · 🔴 >90%).
 */
export function BudgetGauge({ budget }: { budget: Budget }) {
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
          {formatEUR(budget.spent_eur, budget.currency)}
        </span>
        <span className="text-sm text-muted-foreground">
          / {formatEUR(budget.monthly_amount_eur, budget.currency)}
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
            ? `${budget.consumed_pct.toFixed(0)}% consommé`
            : "—"}
        </span>
        <span className="text-muted-foreground">
          Reste {formatEUR(budget.remaining_eur, budget.currency)}
        </span>
      </div>
    </div>
  );
}
