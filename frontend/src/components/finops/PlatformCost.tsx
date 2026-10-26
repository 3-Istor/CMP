"use client";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useFinopsPlatform } from "@/lib/hooks";
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
import { formatEUR } from "./format";

const PERIODS = [
  { value: "30d", label: "30 jours" },
  { value: "90d", label: "90 jours" },
  { value: "ytd", label: "Année en cours" },
];

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
  const [period, setPeriod] = useState("30d");
  const { data, error } = useFinopsPlatform(period, true);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Coût de la plateforme</CardTitle>
        <p className="text-xs text-muted-foreground">
          Services communs et capacité inoccupée, répartis en forfaits fixes
          par application active.
        </p>
        <select
          aria-label="Période du coût de la plateforme"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="w-44 rounded-md border bg-background px-2 py-1 text-sm"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
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
                label="Coût de la plateforme"
                value={formatEUR(data.total_eur)}
                hint={`dont inoccupé ${formatEUR(data.idle_eur)}`}
              />
              <Kpi
                label="Recouvert par les forfaits"
                value={formatEUR(data.recovered_eur)}
              />
              <Kpi
                label="Non couvert"
                value={formatEUR(data.uncovered_eur)}
                hint={data.uncovered_eur < 0 ? "Forfaits en excédent" : undefined}
              />
              <Kpi
                label="Forfait par app"
                value={`${formatEUR(data.fee_per_app_month_eur)} / mois`}
                hint={`${data.active_apps_avg.toFixed(1)} apps actives pour ${data.app_slots} emplacements`}
              />
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-3 py-2 font-medium">Namespace</th>
                    <th className="px-3 py-2 text-right font-medium">Coût</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_namespace.map((n) => (
                    <tr key={n.namespace} className="border-b border-border/50">
                      <td className="px-3 py-2 font-medium">{n.namespace}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {formatEUR(n.eur)}
                      </td>
                    </tr>
                  ))}
                  <tr className="border-b border-border/50">
                    <td className="px-3 py-2 font-medium">Capacité inoccupée</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {formatEUR(data.idle_eur)}
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
                    formatter={(value) => formatEUR(Number(value) || 0)}
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
                    name="Recouvert"
                    stackId="pool"
                    fill="#94A3B8"
                  />
                  <Bar
                    dataKey="uncovered_eur"
                    name="Non couvert"
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
