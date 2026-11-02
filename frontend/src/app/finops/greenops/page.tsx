"use client";

import { CarbonTimeline } from "@/components/finops/CarbonTimeline";
import { DataFreshness } from "@/components/finops/DataFreshness";
import { formatNumber, intlLocale } from "@/components/finops/format";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinopsCarbon, useProjects } from "@/lib/hooks";
import type { FinopsCarbon, FinopsMeta } from "@/types";
import { Activity, AlertCircle, Cloud, Moon, Zap } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

const PERIODS = ["30d", "90d", "ytd"] as const;

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

function CarbonTable({ rows }: { rows: FinopsCarbon["rows"] }) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">{t("common.project")}</th>
            <th className="px-3 py-2 font-medium">{t("common.application")}</th>
            <th className="px-3 py-2 text-right font-medium">
              {t("carbon.energyColumn")}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {t("carbon.directColumn")}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {t("carbon.lcaColumn")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={`${r.project}/${r.app}`}
              className="border-b border-border/50 transition-colors hover:bg-accent/50"
            >
              <td className="px-3 py-2">
                <Badge variant="outline" className="font-normal">
                  {r.project || t("common.na")}
                </Badge>
              </td>
              <td className="px-3 py-2 font-medium">{r.app || t("common.na")}</td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.energy_kwh, 1, locale)} kWh
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.co2e_direct_kg, 2, locale)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.co2e_lca_kg, 2, locale)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GreenopsInner() {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const searchParams = useSearchParams();
  const [project, setProject] = useState(searchParams.get("project") ?? "");
  const [period, setPeriod] = useState("30d");

  const { projects } = useProjects();
  const { data, loading, error } = useFinopsCarbon(project || undefined, period);

  // L'énergie vient d'une puissance mesurée en octobre, donc toujours estimée.
  const meta: FinopsMeta = {
    provider: "real",
    sources: data?.has_estimate
      ? [
          {
            source: "carbon",
            last_ingested_at: null,
            latest_day: null,
            final_through: null,
            has_estimate: true,
          },
        ]
      : [],
  };
  const lca = data?.methods.find((m) => m.id === "lca");
  const isEmpty = data !== null && data.rows.length === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={t("common.project")}
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className={SELECT_CLASS}
        >
          <option value="">{t("common.allProjects")}</option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          aria-label={t("common.period")}
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className={SELECT_CLASS}
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              {t(`common.periods.${p}`)}
            </option>
          ))}
        </select>
        <DataFreshness meta={meta} />
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading && !data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-36 rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-80 rounded-xl" />
        </div>
      ) : data ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Zap className="h-4 w-4" /> {t("carbon.energyTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.energy_kwh, 1, locale)} kWh
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("carbon.energyHint", {
                    period: t(`common.periodsIn.${period}`),
                  })}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Cloud className="h-4 w-4" /> {t("carbon.emissionsTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.co2e_direct_kg, 2, locale)} kg CO₂e
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("carbon.lcaHint", {
                    value: formatNumber(data.totals.co2e_lca_kg, 2, locale),
                  })}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Activity className="h-4 w-4" /> {t("carbon.gridTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.latest ? (
                  <>
                    <span className="text-2xl font-semibold tabular-nums">
                      {formatNumber(data.latest.g_per_kwh, 1, locale)} g/kWh
                    </span>
                    <p className="text-xs text-muted-foreground">
                      {t("carbon.gridHint", {
                        date: new Date(data.latest.day).toLocaleDateString(locale),
                      })}
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {t("carbon.gridEmpty")}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Moon className="h-4 w-4" /> {t("carbon.avoidedTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.co2e_avoided_kg, 2, locale)} kg CO₂e
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("carbon.avoidedHint")}
                </p>
              </CardContent>
            </Card>
          </div>

          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">
              {t("carbon.methodTitle")}
            </span>{" "}
            {t("carbon.method", {
              factor: lca?.g_per_kwh ?? t("common.na"),
              source: lca?.source ?? "",
            })}
          </p>

          {isEmpty ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                {t("carbon.emptyText")}
              </CardContent>
            </Card>
          ) : (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>{t("common.dailyTrend")}</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {t("carbon.trendHint")}
                  </p>
                </CardHeader>
                <CardContent>
                  <CarbonTimeline data={data.timeline} />
                  <p className="mt-3 text-xs text-muted-foreground">
                    {t("carbon.rteExplainer")}
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>{t("carbon.detailTitle")}</CardTitle>
                </CardHeader>
                <CardContent>
                  <CarbonTable rows={data.rows} />
                </CardContent>
              </Card>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}

export default function FinopsGreenopsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <GreenopsInner />
    </Suspense>
  );
}
