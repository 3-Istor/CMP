"use client";

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
}: {
  rows: FinopsCloudEstimate[];
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());

  if (rows.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        {t("cloudEstimate.empty")}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto" tabIndex={0}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-3 py-2 font-medium">{t("common.application")}</th>
              <th className="px-3 py-2 font-medium">
                {t("cloudEstimate.measuredOn")}
              </th>
              <th className="px-3 py-2 text-right font-medium">
                {t("cloudEstimate.averageCpuRam")}
              </th>
              {CLOUDS.map((cloud) => (
                <th key={cloud} className="px-3 py-2 text-right font-medium">
                  {CLOUD_META[cloud].label} {t("common.perMonth")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const best = cheapest(row);
              return (
                <tr key={row.app_id} className="border-b last:border-0">
                  <td className="px-3 py-2">
                    <div className="font-medium">{row.app_name}</div>
                    <div className="text-xs text-muted-foreground">
                      {row.project_id ?? t("common.na")}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {row.measured_on
                      .map((cloud) => CLOUD_META[cloud]?.label ?? cloud)
                      .join(", ")}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-xs text-muted-foreground">
                    {t("cloudEstimate.cpuRam", {
                      cores: row.avg_cores.toFixed(2),
                      ram: row.avg_ram_gib.toFixed(2),
                    })}
                  </td>
                  {CLOUDS.map((cloud) => {
                    const value = row.monthly_eur[cloud];
                    return (
                      <td
                        key={cloud}
                        className={`px-3 py-2 text-right tabular-nums ${
                          cloud === best ? "font-semibold text-emerald-600" : ""
                        }`}
                        title={
                          row.estimated[cloud]
                            ? t("cloudEstimate.provisional")
                            : undefined
                        }
                      >
                        {value === undefined
                          ? t("common.na")
                          : `${formatEUR(value, "EUR", locale)}${
                              row.estimated[cloud] ? " *" : ""
                            }`}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        {t("cloudEstimate.footnote")}
      </p>
    </div>
  );
}
