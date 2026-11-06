"use client";

import { cn } from "@/lib/utils";

import { Badge } from "@/components/ui/badge";
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

  if (apps.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        {t("appCost.empty")}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto" tabIndex={0}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">{t("common.application")}</th>
            <th className="px-3 py-2 font-medium">{t("common.project")}</th>
            <th className="px-3 py-2 text-right font-medium">
              {t("appCost.costPerDay")}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {t("appCost.costPerMonth")}
            </th>
            <th
              className="px-3 py-2 text-right font-medium"
              title={t("resourceHints.platform")}
            >
              {t("appCost.ofWhichPlatform")}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {t("appCost.trend")}
            </th>
          </tr>
        </thead>
        <tbody>
          {apps.map((app) => (
            <tr
              key={app.app_id}
              className="border-b border-border/50 transition-colors hover:bg-accent/50"
            >
              <td className="px-3 py-2 font-medium">{app.name}</td>
              <td className="px-3 py-2">
                {app.project_id ? (
                  <Badge variant="outline" className="font-normal">
                    {app.project_id}
                  </Badge>
                ) : (
                  t("common.na")
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatEUR(app.cost_per_day_eur, "EUR", locale)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatEUR(app.cost_month_estimate_eur, "EUR", locale)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                {formatEUR(app.platform_eur ?? 0, "EUR", locale)}
              </td>
              <td className="px-3 py-2">
                <div className="flex justify-end">
                  <Trend pct={app.trend_pct} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
