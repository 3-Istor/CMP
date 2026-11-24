"use client";

import { cn } from "@/lib/utils";

import { Badge } from "@/components/ui/badge";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
import type { AppCostRow } from "@/types";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { formatEUR, intlLocale } from "./format";

function Trend({ pct }: { pct: number | null }) {
  const t = useTranslations("finops");
  if (pct === null) {
    return (
      <span className="flex items-center gap-0.5 text-muted-foreground">
        <Minus className="h-3 w-3" /> {t("common.na")}
      </span>
    );
  }
  const up = pct >= 0;
  return (
    <span
      className={cn("flex items-center gap-0.5 tabular-nums", up ? "text-red-700 dark:text-red-400" : "text-emerald-700 dark:text-emerald-400")}
    >
      {up ? (
        <ArrowUpRight className="h-3 w-3" />
      ) : (
        <ArrowDownRight className="h-3 w-3" />
      )}
      {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

/** Liste exhaustive des applications, triée par coût mensuel décroissant. */
export function AppCostTable({ apps }: { apps: AppCostRow[] }) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());

  const columns: DataColumn<AppCostRow>[] = [
    {
      key: "name",
      header: t("common.application"),
      cell: (app) => <span className="font-medium">{app.name}</span>,
      sortable: true,
    },
    {
      key: "project_id",
      header: t("common.project"),
      sortable: true,
      cell: (app) =>
        app.project_id ? (
          <Badge variant="outline" className="font-normal">
            {app.project_id}
          </Badge>
        ) : (
          t("common.na")
        ),
    },
    {
      key: "cost_per_day_eur",
      header: t("appCost.costPerDay"),
      align: "right",
      sortable: true,
      cell: (app) => formatEUR(app.cost_per_day_eur, "EUR", locale),
    },
    {
      key: "cost_month_estimate_eur",
      header: t("appCost.costPerMonth"),
      align: "right",
      sortable: true,
      cell: (app) => formatEUR(app.cost_month_estimate_eur, "EUR", locale),
    },
    {
      key: "platform_eur",
      header: <span title={t("resourceHints.platform")}>{t("appCost.ofWhichPlatform")}</span>,
      align: "right",
      sortable: true,
      className: "text-muted-foreground",
      cell: (app) => formatEUR(app.platform_eur ?? 0, "EUR", locale),
    },
    {
      key: "trend_pct",
      header: t("appCost.trend"),
      align: "right",
      sortable: true,
      cell: (app) => (
        <div className="flex justify-end">
          <Trend pct={app.trend_pct} />
        </div>
      ),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={apps}
      rowKey={(app) => app.app_id}
      storageKey="finops.appCost"
      initialSort={{ key: "cost_month_estimate_eur", direction: "desc" }}
      searchable
      searchKeys={["name", "project_id"]}
      empty={{ title: t("appCost.empty") }}
    />
  );
}
