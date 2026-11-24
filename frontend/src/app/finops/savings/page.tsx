"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { DataFreshness } from "@/components/finops/DataFreshness";
import { formatEUR, formatNumber, intlLocale } from "@/components/finops/format";
import {
  SAVINGS_COLORS,
  SavingsTimeline,
} from "@/components/finops/SavingsTimeline";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinopsSavings, useProjects } from "@/lib/hooks";
import type { FinopsMeta, FinopsSavings, FinopsSavingsCategory } from "@/types";
import { AlertCircle, Leaf, PiggyBank, Zap } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

// Le registre est une donnée mesurée, indépendante du mode simulé/réel des coûts.
const LEDGER_META: FinopsMeta = { provider: "real", sources: [] };

const CATEGORY_KEY: Record<FinopsSavingsCategory, string> = {
  offhours: "offhours",
  rightsizing_applied: "rightsizingApplied",
  idle_cleanup: "idleCleanup",
};

const PERIODS = ["30d", "90d", "ytd"] as const;

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

type SavingsRow = FinopsSavings["rows"][number];

function SavingsTable({
  rows,
  loading = false,
}: {
  rows: SavingsRow[];
  loading?: boolean;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const columns: DataColumn<SavingsRow>[] = [
    {
      key: "project",
      header: t("common.project"),
      sortable: true,
      cell: (r) => (
        <Badge variant="outline" className="font-normal">
          {r.project}
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
      key: "category",
      header: t("savings.columns.mechanism"),
      sortable: true,
      cell: (r) =>
        CATEGORY_KEY[r.category]
          ? t(`savings.mechanism.${CATEGORY_KEY[r.category]}`)
          : r.category,
    },
    {
      key: "avoided_eur",
      header: t("savings.columns.avoided"),
      align: "right",
      sortable: true,
      cell: (r) => formatEUR(r.avoided_eur, "EUR", locale),
    },
    {
      key: "realized_eur",
      header: t("savings.columns.realized"),
      align: "right",
      sortable: true,
      cell: (r) => formatEUR(r.realized_eur, "EUR", locale),
    },
    {
      key: "energy_avoided_kwh",
      header: t("savings.columns.kwh"),
      align: "right",
      sortable: true,
      cell: (r) => `${formatNumber(r.energy_avoided_kwh, 1, locale)} kWh`,
    },
    {
      key: "days",
      header: t("savings.columns.days"),
      align: "right",
      sortable: true,
      cell: (r) => r.days,
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.project}/${r.app}/${r.category}`}
      loading={loading}
      storageKey="finops.savings"
      initialSort={{ key: "avoided_eur", direction: "desc" }}
    />
  );
}

function SavingsSkeleton() {
  const t = useTranslations("finops");
  const tiles = [
    { icon: PiggyBank, label: t("savings.seriesAvoided") },
    { icon: Leaf, label: t("savings.seriesRealized") },
    { icon: Zap, label: t("savings.columns.kwh") },
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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
          <p className="text-xs text-muted-foreground">{t("savings.timelineHint")}</p>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-64 rounded-lg" />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("savings.detailTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          <SavingsTable rows={[]} loading />
        </CardContent>
      </Card>
    </div>
  );
}

function SavingsInner() {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const searchParams = useSearchParams();
  const [project, setProject] = useState(searchParams.get("project") ?? "");
  const [period, setPeriod] = useState("ytd");

  const { projects } = useProjects();
  const { data, loading, error } = useFinopsSavings(
    project || undefined,
    period,
  );

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
        <DataFreshness meta={LEDGER_META} compact />
        {data?.since && (
          <span className="text-xs text-muted-foreground">
            {t("savings.firstData", {
              date: new Date(data.since).toLocaleDateString(locale),
            })}
          </span>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading && !data ? (
        <SavingsSkeleton />
      ) : data ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <PiggyBank className="h-4 w-4" /> {t("savings.seriesAvoided")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span
                  className="text-2xl font-semibold tabular-nums"
                  style={{ color: SAVINGS_COLORS.avoided }}
                >
                  {formatEUR(data.totals.avoided_eur, "EUR", locale)}
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("savings.avoidedHint")}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Leaf className="h-4 w-4" /> {t("savings.seriesRealized")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span
                  className="text-2xl font-semibold tabular-nums"
                  style={{ color: SAVINGS_COLORS.realized }}
                >
                  {formatEUR(data.totals.realized_eur, "EUR", locale)}
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("savings.realizedHint")}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("savings.realizedDetail")}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Zap className="h-4 w-4" /> {t("savings.columns.kwh")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.energy_avoided_kwh, 1, locale)} kWh
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("savings.kwhHint", {
                    co2: formatNumber(data.totals.co2e_avoided_kg, 1, locale),
                  })}
                </p>
              </CardContent>
            </Card>
          </div>

          {isEmpty ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                {t("savings.empty")}
              </CardContent>
            </Card>
          ) : (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>{t("common.dailyTrend")}</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {t("savings.timelineHint")}
                  </p>
                </CardHeader>
                <CardContent>
                  <SavingsTimeline data={data.timeline} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>{t("savings.detailTitle")}</CardTitle>
                </CardHeader>
                <CardContent>
                  <SavingsTable rows={data.rows} />
                </CardContent>
              </Card>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}

export default function FinopsSavingsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <SavingsInner />
    </Suspense>
  );
}
