"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import type { FinopsCloud, FinopsCloudEstimate } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import { CLOUDS, CLOUD_META, formatEUR, intlLocale } from "./format";

function cheapest(row: FinopsCloudEstimate): FinopsCloud | null {
  let best: FinopsCloud | null = null;
  for (const cloud of CLOUDS) {
    const value = row.monthly_eur[cloud];
    if (value === undefined) continue;
    if (best === null || value < (row.monthly_eur[best] ?? Infinity))
      best = cloud;
  }
  return best;
}

export function CloudEstimateTable({
  rows,
  loading = false,
}: {
  rows: FinopsCloudEstimate[];
  loading?: boolean;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());

  const columns: DataColumn<FinopsCloudEstimate>[] = [
    {
      key: "app_name",
      header: t("common.application"),
      sortable: true,
      cell: (row) => (
        <>
          <div className="font-medium">{row.app_name}</div>
          <div className="text-xs text-muted-foreground">
            {row.project_id ?? t("common.na")}
          </div>
        </>
      ),
    },
    {
      key: "measured_on",
      header: t("cloudEstimate.measuredOn"),
      className: "text-xs",
      cell: (row) =>
        row.measured_on.map((cloud) => CLOUD_META[cloud]?.label ?? cloud).join(", "),
    },
    {
      key: "avg_cores",
      header: t("cloudEstimate.averageCpuRam"),
      align: "right",
      sortable: true,
      className: "tabular-nums text-xs text-muted-foreground",
      cell: (row) =>
        t("cloudEstimate.cpuRam", {
          cores: row.avg_cores.toFixed(2),
          ram: row.avg_ram_gib.toFixed(2),
        }),
    },
    ...CLOUDS.map(
      (cloud): DataColumn<FinopsCloudEstimate> => ({
        key: `monthly_${cloud}`,
        header: `${CLOUD_META[cloud].label} ${t("common.perMonth")}`,
        align: "right",
        sortable: true,
        sortValue: (row) => row.monthly_eur[cloud],
        className: "tabular-nums",
        cell: (row) => {
          const value = row.monthly_eur[cloud];
          return (
            <span
              className={cloud === cheapest(row) ? "font-semibold text-emerald-600" : ""}
              title={row.estimated[cloud] ? t("cloudEstimate.provisional") : undefined}
            >
              {value === undefined
                ? t("common.na")
                : `${formatEUR(value, "EUR", locale)}${row.estimated[cloud] ? " *" : ""}`}
            </span>
          );
        },
      }),
    ),
  ];

  return (
    <div className="space-y-3">
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.app_id}
        loading={loading}
        storageKey="finops.cloudEstimate"
        searchable
        searchKeys={["app_name", "project_id"]}
        empty={{ title: t("cloudEstimate.empty") }}
      />
      <p className="text-xs text-muted-foreground">{t("cloudEstimate.footnote")}</p>
    </div>
  );
}
