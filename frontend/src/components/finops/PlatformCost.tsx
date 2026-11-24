"use client";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { useFinopsPlatform } from "@/lib/hooks";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEUR, intlLocale } from "./format";

const PERIODS = ["30d", "90d", "ytd"] as const;

function Kpi({
  label,
  value,
  hint,
  loading = false,
}: {
  label: string;
  value?: string | null;
  hint?: string | null;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      {loading ? (
        <div className="my-1 h-6 w-20 animate-pulse rounded bg-muted" aria-hidden />
      ) : (
        <p className="text-lg font-semibold tabular-nums">{value}</p>
      )}
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Réservé à la finance et aux admins: l'API refuse les autres rôles. */
export function PlatformCost() {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const [period, setPeriod] = useState("30d");
  const { data, error } = useFinopsPlatform(period, true);
  const money = (value: number) => formatEUR(value, "EUR", locale);
  const columns: DataColumn<{ namespace: string; eur: number }>[] = [
    {
      key: "namespace",
      header: "Namespace",
      sortable: true,
      className: "font-medium",
      cell: (n) => n.namespace,
    },
    {
      key: "eur",
      header: t("platform.cost"),
      align: "right",
      sortable: true,
      cell: (n) => money(n.eur),
    },
  ];
  const showing = !data && !error;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("platform.title")}</CardTitle>
        <p className="text-xs text-muted-foreground">
          {t("platform.description")}
        </p>
        <select
          aria-label={t("platform.periodAria")}
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="w-44 rounded-md border bg-background px-2 py-1 text-sm"
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              {t(`platform.periods.${p}`)}
            </option>
          ))}
        </select>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {(data || showing) && (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Kpi
                label={t("platform.total")}
                loading={showing}
                value={data && money(data.total_eur)}
                hint={data && t("platform.idleHint", { idle: money(data.idle_eur) })}
              />
              <Kpi
                label={t("platform.covered")}
                loading={showing}
                value={data && money(data.recovered_eur)}
              />
              <Kpi
                label={t("platform.uncovered")}
                loading={showing}
                value={data && money(data.uncovered_eur)}
                hint={
                  data && data.uncovered_eur < 0
                    ? t("platform.uncoveredHint")
                    : undefined
                }
              />
              <Kpi
                label={t("platform.feePerApp")}
                loading={showing}
                value={data && `${money(data.fee_per_app_month_eur)} ${t("common.perMonth")}`}
                hint={
                  data &&
                  t("platform.feeHint", {
                    active: data.active_apps_avg.toFixed(1),
                    slots: data.app_slots,
                  })
                }
              />
            </div>

            <DataTable
              columns={columns}
              rows={
                data
                  ? [...data.by_namespace, { namespace: t("platform.idle"), eur: data.idle_eur }]
                  : []
              }
              rowKey={(n) => n.namespace}
              loading={showing}
              storageKey="finops.platform"
            />

            {data ? (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.daily} stackOffset="sign">
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip
                    formatter={(value) => money(Number(value) || 0)}
                    contentStyle={{
                      borderRadius: 8,
                      fontSize: 12,
                      background: "var(--popover)",
                      border: "1px solid var(--border)",
                    }}
                  />
                  <Legend />
                  <Bar
                    dataKey="recovered_eur"
                    name={t("platform.chartCovered")}
                    stackId="pool"
                    fill="#94A3B8"
                  />
                  <Bar
                    dataKey="uncovered_eur"
                    name={t("platform.chartUncovered")}
                    stackId="pool"
                    fill="#F59E0B"
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
            ) : (
              <div className="h-64 w-full animate-pulse rounded-lg bg-muted" aria-busy />
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
