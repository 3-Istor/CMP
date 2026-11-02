"use client";

import { CarbonTimeline } from "@/components/finops/CarbonTimeline";
import { DataFreshness } from "@/components/finops/DataFreshness";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinopsCarbon, useProjects } from "@/lib/hooks";
import type { FinopsCarbon, FinopsMeta } from "@/types";
import { Activity, AlertCircle, Cloud, Moon, Zap } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

const PERIOD_LABEL: Record<string, string> = {
  "30d": "30 jours",
  "90d": "90 jours",
  ytd: "depuis le 1er janvier",
};

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

function formatNumber(value: number, digits = 1): string {
  return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: digits }).format(
    value,
  );
}

function CarbonTable({ rows }: { rows: FinopsCarbon["rows"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">Projet</th>
            <th className="px-3 py-2 font-medium">Application</th>
            <th className="px-3 py-2 text-right font-medium">Énergie</th>
            <th className="px-3 py-2 text-right font-medium">
              kg CO₂e (direct RTE)
            </th>
            <th className="px-3 py-2 text-right font-medium">kg CO₂e (ACV)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={`${r.project}/${r.app}`}
              className="border-b border-border/50 transition-colors hover:bg-accent/50"
            >
              <td className="px-3 py-2">
                <Badge variant="outline" className="font-normal">
                  {r.project || "—"}
                </Badge>
              </td>
              <td className="px-3 py-2 font-medium">{r.app || "—"}</td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.energy_kwh)} kWh
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.co2e_direct_kg, 2)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.co2e_lca_kg, 2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GreenopsInner() {
  const searchParams = useSearchParams();
  const [project, setProject] = useState(searchParams.get("project") ?? "");
  const [period, setPeriod] = useState("30d");

  const { projects } = useProjects();
  const { data, loading, error } = useFinopsCarbon(project || undefined, period);

  // L'énergie vient d'une puissance mesurée en octobre, donc toujours estimée.
  const meta: FinopsMeta = {
    provider: "real",
    sources: data?.has_estimate
      ? [
          {
            source: "carbon",
            last_ingested_at: null,
            latest_day: null,
            final_through: null,
            has_estimate: true,
          },
        ]
      : [],
  };
  const lca = data?.methods.find((m) => m.id === "lca");
  const isEmpty = data !== null && data.rows.length === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Projet"
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className={SELECT_CLASS}
        >
          <option value="">Tous les projets</option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Période"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className={SELECT_CLASS}
        >
          <option value="30d">30 jours</option>
          <option value="90d">90 jours</option>
          <option value="ytd">Depuis le 1er janvier</option>
        </select>
        <DataFreshness meta={meta} />
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading && !data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-36 rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-80 rounded-xl" />
        </div>
      ) : data ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Zap className="h-4 w-4" /> Énergie consommée
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.energy_kwh)} kWh
                </span>
                <p className="text-xs text-muted-foreground">
                  Sur {PERIOD_LABEL[period]}, serveurs on-prem.
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Cloud className="h-4 w-4" /> Émissions (direct RTE)
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.co2e_direct_kg, 2)} kg CO₂e
                </span>
                <p className="text-xs text-muted-foreground">
                  ACV ADEME : {formatNumber(data.totals.co2e_lca_kg, 2)} kg CO₂e
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Activity className="h-4 w-4" /> Intensité du réseau
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.latest ? (
                  <>
                    <span className="text-2xl font-semibold tabular-nums">
                      {formatNumber(data.latest.g_per_kwh)} g/kWh
                    </span>
                    <p className="text-xs text-muted-foreground">
                      Moyenne du{" "}
                      {new Date(data.latest.day).toLocaleDateString("fr-FR")}
                      , RTE éCO2mix.
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Pas encore de mesure RTE.
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Moon className="h-4 w-4" /> CO₂e évité par la veille
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.co2e_avoided_kg, 2)} kg CO₂e
                </span>
                <p className="text-xs text-muted-foreground">
                  Mise en veille offhours-guard, même intensité RTE.
                </p>
              </CardContent>
            </Card>
          </div>

          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Méthode.</span>{" "}
            On-prem : puissance mesurée des hyperviseurs (moyenne d&apos;octobre,
            en attendant un exporter continu) × intensité RTE éCO2mix du jour.
            ACV : même énergie × {lca?.g_per_kwh ?? "—"} g/kWh ({lca?.source}).
            Cloud : estimation, à venir.
          </p>

          {isEmpty ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                Aucune énergie mesurée sur cette période. Les données
                apparaissent après le premier passage de l&apos;ingester.
              </CardContent>
            </Card>
          ) : (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>Évolution quotidienne</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    Énergie en barres, intensité moyenne du jour en ligne.
                  </p>
                </CardHeader>
                <CardContent>
                  <CarbonTimeline data={data.timeline} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Détail par projet et application</CardTitle>
                </CardHeader>
                <CardContent>
                  <CarbonTable rows={data.rows} />
                </CardContent>
              </Card>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}

export default function FinopsGreenopsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <GreenopsInner />
    </Suspense>
  );
}
