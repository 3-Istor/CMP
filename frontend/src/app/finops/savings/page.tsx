"use client";

import { DataFreshness } from "@/components/finops/DataFreshness";
import { formatEUR } from "@/components/finops/format";
import {
  SAVINGS_COLORS,
  SavingsTimeline,
} from "@/components/finops/SavingsTimeline";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinopsSavings, useProjects } from "@/lib/hooks";
import type { FinopsMeta, FinopsSavings, FinopsSavingsCategory } from "@/types";
import { AlertCircle, Leaf, PiggyBank, Zap } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

// Le registre est une donnée mesurée, indépendante du mode simulé/réel des coûts.
const LEDGER_META: FinopsMeta = { provider: "real", sources: [] };

const CATEGORY_LABEL: Record<FinopsSavingsCategory, string> = {
  offhours: "Mise en veille (offhours)",
  rightsizing_applied: "Right-sizing appliqué",
  idle_cleanup: "Nettoyage des ressources inactives",
};

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

function formatNumber(value: number): string {
  return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(
    value,
  );
}

function SavingsTable({ rows }: { rows: FinopsSavings["rows"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">Projet</th>
            <th className="px-3 py-2 font-medium">Application</th>
            <th className="px-3 py-2 font-medium">Mécanisme</th>
            <th className="px-3 py-2 text-right font-medium">Coût alloué évité</th>
            <th className="px-3 py-2 text-right font-medium">Économie encaissée</th>
            <th className="px-3 py-2 text-right font-medium">kWh évités</th>
            <th className="px-3 py-2 text-right font-medium">Jours</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={`${r.project}/${r.app}/${r.category}`}
              className="border-b border-border/50 transition-colors hover:bg-accent/50"
            >
              <td className="px-3 py-2">
                <Badge variant="outline" className="font-normal">
                  {r.project}
                </Badge>
              </td>
              <td className="px-3 py-2 font-medium">{r.app || "—"}</td>
              <td className="px-3 py-2">
                {CATEGORY_LABEL[r.category] ?? r.category}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatEUR(r.avoided_eur)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatEUR(r.realized_eur)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {formatNumber(r.energy_avoided_kwh)} kWh
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{r.days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SavingsInner() {
  const searchParams = useSearchParams();
  const [project, setProject] = useState(searchParams.get("project") ?? "");
  const [period, setPeriod] = useState("ytd");

  const { projects } = useProjects();
  const { data, loading, error } = useFinopsSavings(
    project || undefined,
    period,
  );

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
        <DataFreshness meta={LEDGER_META} compact />
        {data?.since && (
          <span className="text-xs text-muted-foreground">
            Premières données le{" "}
            {new Date(data.since).toLocaleDateString("fr-FR")}
          </span>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading && !data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-36 rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-80 rounded-xl" />
        </div>
      ) : data ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <PiggyBank className="h-4 w-4" /> Coût alloué évité
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span
                  className="text-2xl font-semibold tabular-nums"
                  style={{ color: SAVINGS_COLORS.avoided }}
                >
                  {formatEUR(data.totals.avoided_eur)}
                </span>
                <p className="text-xs text-muted-foreground">
                  Capacité libérée pendant la veille. Ne baisse la facture que
                  si des nœuds disparaissent.
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Leaf className="h-4 w-4" /> Économie encaissée
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span
                  className="text-2xl font-semibold tabular-nums"
                  style={{ color: SAVINGS_COLORS.realized }}
                >
                  {formatEUR(data.totals.realized_eur)}
                </span>
                <p className="text-xs text-muted-foreground">
                  Électricité non consommée on-prem. 0 € dans le cloud sans
                  autoscaler.
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-muted-foreground">
                  <Zap className="h-4 w-4" /> kWh évités
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {formatNumber(data.totals.energy_avoided_kwh)} kWh
                </span>
                <p className="text-xs text-muted-foreground">
                  Soit {formatNumber(data.totals.co2e_avoided_kg)} kg CO₂e
                  évités.
                </p>
              </CardContent>
            </Card>
          </div>

          {isEmpty ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                Aucune économie enregistrée sur cette période. Les économies
                apparaissent le lendemain du jour où offhours-guard met des
                charges de travail en veille.
              </CardContent>
            </Card>
          ) : (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>Évolution quotidienne</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    Deux séries distinctes, jamais additionnées.
                  </p>
                </CardHeader>
                <CardContent>
                  <SavingsTimeline data={data.timeline} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Détail par projet, application et mécanisme</CardTitle>
                </CardHeader>
                <CardContent>
                  <SavingsTable rows={data.rows} />
                </CardContent>
              </Card>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}

export default function FinopsSavingsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <SavingsInner />
    </Suspense>
  );
}
