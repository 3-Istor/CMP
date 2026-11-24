"use client";

import { CarbonTimeline } from "@/components/finops/CarbonTimeline";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
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

type CarbonRow = FinopsCarbon["rows"][number];

function CarbonTable({
  rows,
  loading = false,
}: {
  rows: CarbonRow[];
  loading?: boolean;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const columns: DataColumn<CarbonRow>[] = [
    {
      key: "project",
      header: t("common.project"),
      sortable: true,
      cell: (r) => (
        <Badge variant="outline" className="font-normal">
          {r.project || t("common.na")}
        </Badge>
      ),
    },
    {
      key: "app",
      header: t("common.application"),
      sortable: true,
      className: "font-medium",
      cell: (r) => r.app || t("common.na"),
    },
    {
      key: "energy_kwh",
      header: t("carbon.energyColumn"),
      align: "right",
      sortable: true,
      cell: (r) => `${formatNumber(r.energy_kwh, 1, locale)} kWh`,
    },
    {
      key: "co2e_direct_kg",
      header: t("carbon.directColumn"),
      align: "right",
      sortable: true,
      cell: (r) => formatNumber(r.co2e_direct_kg, 2, locale),
    },
    {
      key: "co2e_lca_kg",
      header: t("carbon.lcaColumn"),
      align: "right",
      sortable: true,
      cell: (r) => formatNumber(r.co2e_lca_kg, 2, locale),
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.project}/${r.app}`}
      loading={loading}
      storageKey="finops.greenops"
      initialSort={{ key: "energy_kwh", direction: "desc" }}
    />
  );
}

function CarbonSkeleton() {
  const t = useTranslations("finops");
  const tiles = [
    { icon: Zap, label: t("carbon.energyTitle") },
    { icon: Cloud, label: t("carbon.emissionsTitle") },
    { icon: Activity, label: t("carbon.gridTitle") },
    { icon: Moon, label: t("carbon.avoidedTitle") },
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {tiles.map(({ icon: Icon, label }) => (
          <Card key={label}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-muted-foreground">
                <Icon className="h-4 w-4" /> {label}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Skeleton className="h-8 w-28" />
              <Skeleton className="h-4 w-48" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t("common.dailyTrend")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("carbon.trendHint")}</p>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-64 rounded-lg" />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("carbon.detailTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          <CarbonTable rows={[]} loading />
        </CardContent>
      </Card>
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
        <CarbonSkeleton />
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
