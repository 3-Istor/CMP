"use client";

import { BudgetForm } from "@/components/finops/BudgetForm";
import { BudgetGauge } from "@/components/finops/BudgetGauge";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getBudget, getFinopsAlerts } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import type { Budget, CostAlert } from "@/types";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

const LEVEL_COLOR: Record<string, string> = {
  info: "#3B82F6",
  warning: "#F59E0B",
  critical: "#EF4444",
};

function BudgetsInner() {
  const searchParams = useSearchParams();
  const { projects } = useProjects();
  const [project, setProject] = useState(searchParams.get("project") ?? "");

  const [budget, setBudget] = useState<Budget | null>(null);
  const [alerts, setAlerts] = useState<CostAlert[]>([]);
  const [loading, setLoading] = useState(false);

  // Default to the first owned project once the list loads.
  useEffect(() => {
    if (!project && projects.length > 0) {
      const owned = projects.find((p) => p.role === "owner") ?? projects[0];
      setProject(owned.name);
    }
  }, [projects, project]);

  const canEdit = projects.find((p) => p.name === project)?.role === "owner";

  const refresh = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    try {
      const [b, a] = await Promise.all([
        getBudget(project),
        getFinopsAlerts(project),
      ]);
      setBudget(b);
      setAlerts(a);
    } catch {
      // access errors surface as empty state
      setBudget(null);
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const selectCls =
    "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <label className="text-sm text-muted-foreground">Projet</label>
        <select
          aria-label="Projet"
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className={selectCls}
        >
          <option value="" disabled>
            Sélectionner…
          </option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
              {p.role === "owner" ? " (owner)" : ""}
            </option>
          ))}
        </select>
      </div>

      {!project ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Sélectionnez un projet pour gérer son budget.
          </CardContent>
        </Card>
      ) : loading && !budget ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Config budget */}
          <Card>
            <CardHeader>
              <CardTitle>Budget mensuel — {project}</CardTitle>
              <CardDescription>
                Définissez l&apos;enveloppe et les seuils d&apos;alerte du projet.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {budget && budget.monthly_amount_eur > 0 && (
                <BudgetGauge budget={budget} />
              )}
              <BudgetForm
                projectName={project}
                budget={budget}
                canEdit={!!canEdit}
                onSaved={(b) => {
                  setBudget(b);
                  refresh();
                }}
              />
            </CardContent>
          </Card>

          {/* Historique des alertes */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldAlert className="h-4 w-4" /> Historique des alertes
              </CardTitle>
            </CardHeader>
            <CardContent>
              {alerts.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  Aucune alerte enregistrée.
                </p>
              ) : (
                <ul className="space-y-2">
                  {alerts.map((a) => (
                    <li
                      key={a.id}
                      className="flex items-start gap-2 rounded-lg border p-2.5 text-sm"
                    >
                      <AlertTriangle
                        className="mt-0.5 h-4 w-4 shrink-0"
                        style={{ color: LEVEL_COLOR[a.level] ?? "#3B82F6" }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <Badge
                            variant="outline"
                            className="capitalize"
                            style={{ color: LEVEL_COLOR[a.level] }}
                          >
                            {a.kind}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {new Date(a.triggered_at).toLocaleString("fr-FR")}
                          </span>
                          {a.value_pct !== null && (
                            <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                              {a.value_pct}%
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5">{a.message}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

export default function FinopsBudgetsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <BudgetsInner />
    </Suspense>
  );
}
