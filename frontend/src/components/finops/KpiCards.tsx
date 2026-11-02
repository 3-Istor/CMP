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
import { useLocale, useTranslations } from "next-intl";
import { BudgetGauge } from "./BudgetGauge";
import { DataFreshness } from "./DataFreshness";
import {
  RESOURCE_META,
  STATUS_COLOR,
  formatEUR,
  formatPct,
  intlLocale,
  parisClock,
  visibleResources,
} from "./format";

function ForecastLine({
  forecast,
  currency,
}: {
  forecast: CostForecast;
  currency: string;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const money = (value: number) => formatEUR(value, currency, locale);
  const spread = forecast.high_eur - forecast.projected_eur;
  const overrun = forecast.overrun_date;
  const today = new Date().toISOString().slice(0, 10);
  const confidence = Math.round(forecast.confidence * 100);
  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      <p>
        {t("kpi.forecastEnd")}{" "}
        <span className="font-medium text-foreground tabular-nums">
          {money(forecast.projected_eur)}
        </span>
        {spread > 0 && (
          <span className="tabular-nums">
            {" "}
            {t("kpi.forecastSpread", { amount: money(spread) })}
          </span>
        )}
      </p>
      <p
        className="tabular-nums"
        title={t("kpi.forecastInterval", {
          confidence,
          days: forecast.days_of_history,
        })}
      >
        {t("kpi.forecastRange", {
          low: money(forecast.low_eur),
          high: money(forecast.high_eur),
          confidence,
        })}
        {forecast.low_history && ` ${t("kpi.forecastShortHistory")}`}
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
            ? t("kpi.overrunPast", { date: formatDay(overrun, locale) })
            : t("kpi.overrunFuture", { date: formatDay(overrun, locale) })}
        </span>
      )}
    </div>
  );
}

function formatDay(iso: string, locale: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(locale, {
    day: "numeric",
    month: "long",
  });
}

function dataStartInMonth(meta: FinopsMeta | null): string | null {
  const days = (meta?.sources ?? [])
    .map((s) => s.first_day)
    .filter((d): d is string => !!d)
    .sort();
  const first = days[0];
  if (!first || first.slice(0, 7) !== parisClock(new Date()).date.slice(0, 7)) {
    return null;
  }
  if (first.endsWith("-01")) return null;
  const [, month, day] = first.split("-");
  return `${day}/${month}`;
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
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const money = (value: number) => formatEUR(value, summary.currency, locale);
  const trend = summary.trend_pct;
  const trendUp = (trend ?? 0) >= 0;
  const since = dataStartInMonth(meta);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
      {/* 1 · Coût total du mois */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <Wallet className="h-4 w-4" /> {t("kpi.monthCost")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-semibold tabular-nums">
              {money(summary.month_to_date_eur)}
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
          {since && (
            <p className="text-xs text-muted-foreground">
              {t("kpi.sinceDay", { day: since })}
            </p>
          )}
          {forecast ? (
            <ForecastLine forecast={forecast} currency={summary.currency} />
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("kpi.projection", {
                amount: money(summary.projected_month_eur),
              })}
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
                title={r === "platform" ? t("resourceHints.platform") : undefined}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px]"
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: RESOURCE_META[r].color }}
                />
                {t(`resources.${r}`)} {money(summary.breakdown[r] ?? 0)}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 2 · Budget alloué vs consommé */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <PiggyBank className="h-4 w-4" /> {t("kpi.budgetTitle")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {budget ? (
            <BudgetGauge budget={budget} />
          ) : (
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>{t("kpi.noBudget")}</p>
              {project && (
                <Link
                  href={`/finops/budgets?project=${encodeURIComponent(project)}`}
                  className="text-primary hover:underline"
                >
                  {t("kpi.setBudget")}
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
            <Boxes className="h-4 w-4" /> {t("kpi.trackedApps")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <span className="text-2xl font-semibold tabular-nums">
            {summary.app_count}
          </span>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("kpi.trackedAppsHint")}
          </p>
        </CardContent>
      </Card>

      {/* 4 · Économies potentielles */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <PiggyBank className="h-4 w-4" /> {t("kpi.potentialSavings")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <span
            className="text-2xl font-semibold tabular-nums"
            style={{ color: "#10B981" }}
          >
            {money(summary.potential_savings_eur)}
            <span className="text-sm font-normal text-muted-foreground">
              {" "}
              {t("common.perMonth")}
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
            {t("kpi.seeRecommendations")}
          </Link>
        </CardContent>
      </Card>
      {/* 5 · Économies encaissées (YTD), évité en ligne secondaire */}
      {savings && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-muted-foreground">
              <Leaf className="h-4 w-4" /> {t("kpi.realisedSavings")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <span
              className="text-2xl font-semibold tabular-nums"
              style={{ color: "#10B981" }}
            >
              {formatEUR(savings.realized_eur, summary.currency, locale)}
            </span>
            <p className="text-xs text-muted-foreground">
              {t("kpi.allocatedAvoided", {
                amount: formatEUR(savings.avoided_eur, summary.currency, locale),
              })}
            </p>
            <Link
              href={
                project
                  ? `/finops/savings?project=${encodeURIComponent(project)}`
                  : "/finops/savings"
              }
              className="block text-xs text-primary hover:underline"
            >
              {t("kpi.seeDetails")}
            </Link>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
