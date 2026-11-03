"use client";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
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
        {data && (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Kpi
                label={t("platform.total")}
                value={money(data.total_eur)}
                hint={t("platform.idleHint", { idle: money(data.idle_eur) })}
              />
              <Kpi
                label={t("platform.covered")}
                value={money(data.recovered_eur)}
              />
              <Kpi
                label={t("platform.uncovered")}
                value={money(data.uncovered_eur)}
                hint={
                  data.uncovered_eur < 0
                    ? t("platform.uncoveredHint")
                    : undefined
                }
              />
              <Kpi
                label={t("platform.feePerApp")}
                value={`${money(data.fee_per_app_month_eur)} ${t("common.perMonth")}`}
                hint={t("platform.feeHint", {
                  active: data.active_apps_avg.toFixed(1),
                  slots: data.app_slots,
                })}
              />
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-3 py-2 font-medium">Namespace</th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("platform.cost")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_namespace.map((n) => (
                    <tr key={n.namespace} className="border-b border-border/50">
                      <td className="px-3 py-2 font-medium">{n.namespace}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {money(n.eur)}
                      </td>
                    </tr>
                  ))}
                  <tr className="border-b border-border/50">
                    <td className="px-3 py-2 font-medium">{t("platform.idle")}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {money(data.idle_eur)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

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
          </>
        )}
      </CardContent>
    </Card>
  );
}
