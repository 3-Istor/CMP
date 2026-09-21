"use client";

import { Badge } from "@/components/ui/badge";
import type { AppCostRow } from "@/types";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { formatEUR } from "./format";

function Trend({ pct }: { pct: number | null }) {
  if (pct === null) {
    return (
      <span className="flex items-center gap-0.5 text-muted-foreground">
        <Minus className="h-3 w-3" /> —
      </span>
    );
  }
  const up = pct >= 0;
  return (
    <span
      className="flex items-center gap-0.5 tabular-nums"
      style={{ color: up ? "#EF4444" : "#10B981" }}
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
  if (apps.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Aucune application dans ce périmètre.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">Application</th>
            <th className="px-3 py-2 font-medium">Projet</th>
            <th className="px-3 py-2 text-right font-medium">Coût / jour</th>
            <th className="px-3 py-2 text-right font-medium">Coût / mois est.</th>
            <th className="px-3 py-2 text-right font-medium">Tendance</th>
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
                  "—"
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatEUR(app.cost_per_day_eur)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatEUR(app.cost_month_estimate_eur)}
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
