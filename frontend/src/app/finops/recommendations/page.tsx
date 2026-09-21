"use client";

import { RecommendationCard } from "@/components/finops/RecommendationCard";
import { formatEUR } from "@/components/finops/format";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getRecommendations } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { Recommendation } from "@/types";
import { AlertCircle, Lightbulb } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

type SortKey = "saving" | "confidence" | "effort";
const EFFORT_RANK: Record<string, number> = { low: 0, medium: 1, high: 2 };

function RecommendationsInner() {
  const searchParams = useSearchParams();
  const [project, setProject] = useState(searchParams.get("project") ?? "");
  const [sort, setSort] = useState<SortKey>("saving");
  const [hideDone, setHideDone] = useState(true);

  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { projects } = useProjects();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getRecommendations(project || undefined);
      setRecs(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec du chargement");
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const visible = useMemo(() => {
    const filtered = hideDone
      ? recs.filter((r) => r.status === "pending")
      : recs;
    return [...filtered].sort((a, b) => {
      if (sort === "saving") return b.monthly_saving_eur - a.monthly_saving_eur;
      if (sort === "confidence") return b.confidence - a.confidence;
      return EFFORT_RANK[a.effort] - EFFORT_RANK[b.effort];
    });
  }, [recs, hideDone, sort]);

  const totalSaving = visible.reduce((s, r) => s + r.monthly_saving_eur, 0);

  const selectCls =
    "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

  return (
    <div className="space-y-6">
      {/* Filtres */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Projet"
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className={selectCls}
        >
          <option value="">Tous les projets</option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Trier par"
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className={selectCls}
        >
          <option value="saving">Tri : économie potentielle</option>
          <option value="confidence">Tri : niveau de confiance</option>
          <option value="effort">Tri : effort requis</option>
        </select>
        <button
          onClick={() => setHideDone((v) => !v)}
          className={cn(
            "h-8 rounded-lg border px-2.5 text-sm transition-colors",
            hideDone
              ? "border-input text-muted-foreground hover:bg-accent"
              : "border-primary/40 bg-primary/10 text-primary",
          )}
        >
          {hideDone ? "Masquer traitées" : "Afficher tout"}
        </button>

        <div className="ml-auto text-sm">
          <span className="text-muted-foreground">Économies affichées : </span>
          <span className="font-semibold" style={{ color: "#10B981" }}>
            {formatEUR(totalSaving)} / mois
          </span>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-56 rounded-xl" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center py-12 text-center text-muted-foreground">
            <Lightbulb className="mb-3 h-8 w-8" />
            <p className="font-medium">Aucune recommandation en attente</p>
            <p className="text-sm">
              Vos applications sont correctement dimensionnées pour ce périmètre.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {visible.map((rec) => (
            <RecommendationCard key={rec.id} rec={rec} onChanged={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FinopsRecommendationsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <RecommendationsInner />
    </Suspense>
  );
}
