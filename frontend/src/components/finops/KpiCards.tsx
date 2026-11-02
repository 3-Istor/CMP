"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  Budget,
  CostForecast,
  CostSummary,
  FinopsMeta,
  FinopsSavingsYtd,
} from "@/types";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Boxes,
  PiggyBank,
  Leaf,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { BudgetGauge } from "./BudgetGauge";
import { DataFreshness } from "./DataFreshness";
import {
  RESOURCE_META,
  STATUS_COLOR,
  formatEUR,
  formatPct,
  visibleResources,
} from "./format";

function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "long",
  });
}

function ForecastLine({
  forecast,
  currency,
}: {
  forecast: CostForecast;
  currency: string;
}) {
  const spread = forecast.high_eur - forecast.projected_eur;
  const overrun = forecast.overrun_date;
  const today = new Date().toISOString().slice(0, 10);
  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      <p>
        Prévision fin de mois :{" "}
        <span className="font-medium text-foreground tabular-nums">
          {formatEUR(forecast.projected_eur, currency)}
        </span>
        {spread > 0 && (
          <span className="tabular-nums">
            {" "}
            ± {formatEUR(spread, currency)}
          </span>
        )}
      </p>
      <p
        className="tabular-nums"
        title={`Intervalle à ${Math.round(forecast.confidence * 100)} %, ${forecast.days_of_history} jours d'historique`}
      >
        {formatEUR(forecast.low_eur, currency)} –{" "}
        {formatEUR(forecast.high_eur, currency)} (
        {Math.round(forecast.confidence * 100)} %)
        {forecast.low_history && " · historique court"}
      </p>
      {overrun && (
        <span
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium"
          style={{
            color: overrun < today ? STATUS_COLOR.critical : "#F97316",
            backgroundColor:
              overrun < today
                ? "rgba(239, 68, 68, 0.12)"
                : "rgba(249, 115, 22, 0.12)",
          }}
        >
          <AlertTriangle className="h-3 w-3" />
          {overrun < today
            ? `budget dépassé le ${formatDay(overrun)}`
            : `dépassement prévu le ${formatDay(overrun)}`}
        </span>
      )}
    </div>
  );
}

export function KpiCards({
  summary,
  budget,
  project,
  meta = null,
  savings = null,
  forecast = null,
}: {
  summary: CostSummary;
  budget: Budget | null;
  project?: string;
  meta?: FinopsMeta | null;
  savings?: FinopsSavingsYtd | null;
  forecast?: CostForecast | null;
}) {
  const trend = summary.trend_pct;
  const trendUp = (trend ?? 0) >= 0;

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
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
          {forecast ? (
            <ForecastLine forecast={forecast} currency={summary.currency} />
          ) : (
            <p className="text-xs text-muted-foreground">
              Projection fin de mois :{" "}
              {formatEUR(summary.projected_month_eur, summary.currency)}
            </p>
          )}
          <DataFreshness meta={meta} />
          <div className="flex flex-wrap gap-1.5">
            {visibleResources(
              (summary.breakdown.ai ?? 0) > 0,
              (summary.breakdown.platform ?? 0) > 0,
            ).map((r) => (
              <span
                key={r}
                title={RESOURCE_META[r].hint}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px]"
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: RESOURCE_META[r].color }}
                />
                {RESOURCE_META[r].label}{" "}
                {formatEUR(summary.breakdown[r] ?? 0, summary.currency)}
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
      {/* 5 · Économies encaissées (YTD), évité en ligne secondaire */}
      {savings && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-muted-foreground">
              <Leaf className="h-4 w-4" /> Économies encaissées (YTD)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <span
              className="text-2xl font-semibold tabular-nums"
              style={{ color: "#10B981" }}
            >
              {formatEUR(savings.realized_eur, summary.currency)}
            </span>
            <p className="text-xs text-muted-foreground">
              + {formatEUR(savings.avoided_eur, summary.currency)} alloués
              évités
            </p>
            <Link
              href={
                project
                  ? `/finops/savings?project=${encodeURIComponent(project)}`
                  : "/finops/savings"
              }
              className="block text-xs text-primary hover:underline"
            >
              Voir le détail →
            </Link>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
