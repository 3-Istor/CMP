"use client";

import { AppCostTable } from "@/components/finops/AppCostTable";
import { CostDonut } from "@/components/finops/CostDonut";
import { CostTimeline } from "@/components/finops/CostTimeline";
import {
  FinopsFilters,
  type FinopsFilterState,
} from "@/components/finops/FinopsFilters";
import { KpiCards } from "@/components/finops/KpiCards";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinopsOverview, useProjects } from "@/lib/hooks";
import type { FinopsResource } from "@/types";
import { AlertCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function OverviewInner() {
  const searchParams = useSearchParams();
  const initialProject = searchParams.get("project") ?? "";

  const [filters, setFilters] = useState<FinopsFilterState>({
    project: initialProject,
    period: "30d",
    granularity: "daily",
  });
  const [selectedResource, setSelectedResource] =
    useState<FinopsResource | null>(null);

  const { projects } = useProjects();
  const { data, loading, error } = useFinopsOverview(
    filters.project || undefined,
    filters.period,
    filters.granularity,
  );

  return (
    <div className="space-y-6">
      <FinopsFilters state={filters} onChange={setFilters} projects={projects} />

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
              <Skeleton key={i} className="h-40 rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-80 rounded-xl" />
        </div>
      ) : data ? (
        <>
          <KpiCards
            summary={data.summary}
            budget={data.budget}
            project={filters.project || undefined}
          />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Évolution des dépenses</CardTitle>
                {selectedResource && (
                  <button
                    onClick={() => setSelectedResource(null)}
                    className="text-xs text-primary hover:underline"
                  >
                    Réinitialiser le filtre
                  </button>
                )}
              </CardHeader>
              <CardContent>
                <CostTimeline
                  data={data.timeline}
                  focus={selectedResource}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Distribution des coûts</CardTitle>
              </CardHeader>
              <CardContent>
                <CostDonut
                  breakdown={data.summary.breakdown}
                  selected={selectedResource}
                  onSelect={setSelectedResource}
                />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Suivi des applications</CardTitle>
            </CardHeader>
            <CardContent>
              <AppCostTable apps={data.apps} />
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}

export default function FinopsOverviewPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <OverviewInner />
    </Suspense>
  );
}
