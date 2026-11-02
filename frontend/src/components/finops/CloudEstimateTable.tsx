"use client";

import type { FinopsCloud, FinopsCloudEstimate } from "@/types";
import { CLOUDS, CLOUD_META, formatEUR } from "./format";

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
  if (rows.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Pas encore assez de mesures pour estimer les applications.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-3 py-2 font-medium">Application</th>
              <th className="px-3 py-2 font-medium">Mesurée sur</th>
              <th className="px-3 py-2 text-right font-medium">
                CPU / RAM moyens
              </th>
              {CLOUDS.map((cloud) => (
                <th key={cloud} className="px-3 py-2 text-right font-medium">
                  {CLOUD_META[cloud].label} / mois
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
                      {row.project_id ?? "—"}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {row.measured_on
                      .map((cloud) => CLOUD_META[cloud]?.label ?? cloud)
                      .join(", ")}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-xs text-muted-foreground">
                    {row.avg_cores.toFixed(2)} cœur ·{" "}
                    {row.avg_ram_gib.toFixed(2)} Gio
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
                            ? "Prix à confirmer : valeur provisoire"
                            : undefined
                        }
                      >
                        {value === undefined
                          ? "—"
                          : `${formatEUR(value, "EUR")}${
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
        CPU et mémoire moyens des 30 derniers jours, au prix de chaque cible
        (TCO on-prem, prix catalogue des nœuds EKS et GKE). Hors stockage,
        réseau et forfait plateforme. * prix provisoire.
      </p>
    </div>
  );
}
