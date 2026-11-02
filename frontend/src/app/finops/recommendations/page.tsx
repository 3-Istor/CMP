"use client";

import { RecommendationCard } from "@/components/finops/RecommendationCard";
import { formatEUR, intlLocale } from "@/components/finops/format";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getRecommendations } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import { can } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import type { ProjectRole, Recommendation } from "@/types";
import { AlertCircle, Lightbulb } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

type SortKey = "saving" | "confidence" | "effort";
const EFFORT_RANK: Record<string, number> = { low: 0, medium: 1, high: 2 };

function RecommendationsInner() {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
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
      setError(err instanceof Error ? err.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [project, t]);

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

  // Each recommendation belongs to a different project, so the role is looked
  // up per card rather than once for the page.
  const roleByProject = useMemo(() => {
    const map = new Map<string, ProjectRole>();
    for (const p of projects) map.set(p.name, p.role);
    return map;
  }, [projects]);

  const selectCls =
    "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

  return (
    <div className="space-y-6">
      {/* Filtres */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={t("common.project")}
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className={selectCls}
        >
          <option value="">{t("common.allProjects")}</option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          aria-label={t("recommendations.sortLabel")}
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className={selectCls}
        >
          <option value="saving">{t("recommendations.sort.saving")}</option>
          <option value="confidence">{t("recommendations.sort.confidence")}</option>
          <option value="effort">{t("recommendations.sort.effort")}</option>
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
          {hideDone
            ? t("recommendations.hideDone")
            : t("recommendations.showAll")}
        </button>

        <div className="ml-auto text-sm">
          <span className="text-muted-foreground">
            {t("recommendations.shownSavings")}{" "}
          </span>
          <span className="font-semibold" style={{ color: "#10B981" }}>
            {formatEUR(totalSaving, "EUR", locale)} {t("common.perMonth")}
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
            <p className="font-medium">{t("recommendations.emptyTitle")}</p>
            <p className="text-sm">{t("recommendations.emptyText")}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {visible.map((rec) => (
            <RecommendationCard
              key={rec.id}
              rec={rec}
              onChanged={refresh}
              canAct={can(
                rec.project_id
                  ? roleByProject.get(rec.project_id)
                  : undefined,
                "finops.act",
              )}
            />
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
