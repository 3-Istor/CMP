"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Budget, CostSummary } from "@/types";
import {
  ArrowDownRight,
  ArrowUpRight,
  Boxes,
  PiggyBank,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { BudgetGauge } from "./BudgetGauge";
import { RESOURCE_META, RESOURCES, formatEUR, formatPct } from "./format";

export function KpiCards({
  summary,
  budget,
  project,
}: {
  summary: CostSummary;
  budget: Budget | null;
  project?: string;
}) {
  const trend = summary.trend_pct;
  const trendUp = (trend ?? 0) >= 0;

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
      {/* 1 · Coût total du mois */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <Wallet className="h-4 w-4" /> Coût du mois en cours
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-semibold tabular-nums">
              {formatEUR(summary.month_to_date_eur, summary.currency)}
            </span>
            {trend !== null && (
              <span
                className="flex items-center gap-0.5 text-xs font-medium"
                style={{ color: trendUp ? "#EF4444" : "#10B981" }}
              >
                {trendUp ? (
                  <ArrowUpRight className="h-3 w-3" />
                ) : (
                  <ArrowDownRight className="h-3 w-3" />
                )}
                {formatPct(trend)}
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Projection fin de mois :{" "}
            {formatEUR(summary.projected_month_eur, summary.currency)}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {RESOURCES.map((r) => (
              <span
                key={r}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px]"
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: RESOURCE_META[r].color }}
                />
                {RESOURCE_META[r].label}{" "}
                {formatEUR(summary.breakdown[r], summary.currency)}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 2 · Budget alloué vs consommé */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <PiggyBank className="h-4 w-4" /> Budget alloué vs consommé
          </CardTitle>
        </CardHeader>
        <CardContent>
          {budget ? (
            <BudgetGauge budget={budget} />
          ) : (
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>Aucun budget défini pour ce projet.</p>
              {project && (
                <Link
                  href={`/finops/budgets?project=${encodeURIComponent(project)}`}
                  className="text-primary hover:underline"
                >
                  Définir un budget →
                </Link>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 3 · Applications suivies */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <Boxes className="h-4 w-4" /> Applications suivies
          </CardTitle>
        </CardHeader>
        <CardContent>
          <span className="text-2xl font-semibold tabular-nums">
            {summary.app_count}
          </span>
          <p className="mt-1 text-xs text-muted-foreground">
            Coût réparti sur l&apos;ensemble des applications actives du
            périmètre.
          </p>
        </CardContent>
      </Card>

      {/* 4 · Économies potentielles */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <PiggyBank className="h-4 w-4" /> Économies potentielles
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <span
            className="text-2xl font-semibold tabular-nums"
            style={{ color: "#10B981" }}
          >
            {formatEUR(summary.potential_savings_eur, summary.currency)}
            <span className="text-sm font-normal text-muted-foreground">
              {" "}
              / mois
            </span>
          </span>
          <Link
            href={
              project
                ? `/finops/recommendations?project=${encodeURIComponent(project)}`
                : "/finops/recommendations"
            }
            className="block text-xs text-primary hover:underline"
          >
            Voir les recommandations →
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
