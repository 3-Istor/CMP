"use client";

import { CostCharts } from "@/components/finops/CostCharts";
import {
  FinopsFilters,
  type FinopsFilterState,
} from "@/components/finops/FinopsFilters";
import { RecommendationCard } from "@/components/finops/RecommendationCard";
import { OpenDashboardButton } from "@/components/ui/open-dashboard-button";
import { Skeleton } from "@/components/ui/skeleton";
import { getRecommendations } from "@/lib/api";
import { useFinopsMeta, useFinopsOverview } from "@/lib/hooks";
import type { Recommendation } from "@/types";
import { AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

/** One app's spend, its breakdown and its pending recommendations. */
export function AppCostsPanel({
  project,
  appId,
  canAct,
}: {
  project: string;
  appId: number;
  canAct: boolean;
}) {
  const t = useTranslations("finops");
  const [filters, setFilters] = useState<FinopsFilterState>({
    project,
    period: "30d",
    granularity: "daily",
    cloud: "",
  });
  const { data, loading, error } = useFinopsOverview(
    project,
    filters.period,
    filters.granularity,
    undefined,
    appId,
  );
  const meta = useFinopsMeta();
  const [recs, setRecs] = useState<Recommendation[]>([]);

  const loadRecs = useCallback(() => {
    getRecommendations(project, appId)
      .then((all) => setRecs(all.filter((r) => r.status === "pending")))
      .catch(() => setRecs([]));
  }, [project, appId]);

  useEffect(() => {
    loadRecs();
  }, [loadRecs]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <FinopsFilters
          state={filters}
          onChange={setFilters}
          projects={[]}
          showProject={false}
        />
        <OpenDashboardButton
          href={`/projects/${encodeURIComponent(project)}/costs`}
          label={t("appPanel.projectCosts")}
        />
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading && !data ? (
        <Skeleton className="h-80 rounded-xl" />
      ) : data ? (
        <CostCharts
          timeline={data.timeline}
          breakdown={data.summary.breakdown}
          forecast={
            filters.granularity === "daily" ? (data.forecast ?? null) : null
          }
          granularity={filters.granularity}
          anomalies={[]}
          meta={meta}
        />
      ) : null}

      {recs.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-base font-semibold">
            {t("appPanel.recommendations")}
          </h2>
          {recs.map((rec) => (
            <RecommendationCard
              key={rec.id}
              rec={rec}
              canAct={canAct}
              onChanged={loadRecs}
            />
          ))}
        </section>
      )}
    </div>
  );
}
