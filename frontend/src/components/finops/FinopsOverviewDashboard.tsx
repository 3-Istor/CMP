"use client";

import { AppCostTable } from "@/components/finops/AppCostTable";
import { CostCharts } from "@/components/finops/CostCharts";
import {
  FinopsFilters,
  type FinopsFilterState,
} from "@/components/finops/FinopsFilters";
import { CloudEstimateTable } from "@/components/finops/CloudEstimateTable";
import { CloudSplit } from "@/components/finops/CloudSplit";
import { KpiCards } from "@/components/finops/KpiCards";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { downloadFinopsCsv } from "@/lib/api";
import {
  useFinopsAnomalies,
  useFinopsEstimates,
  useFinopsMeta,
  useFinopsOverview,
  useProjects,
} from "@/lib/hooks";
import { AlertCircle, Download } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

export function FinopsOverviewDashboard({
  initialProject = "",
  fixedProject,
  actions,
}: {
  initialProject?: string;
  /** Verrouille le projet et masque le filtre projet. */
  fixedProject?: string;
  actions?: ReactNode;
}) {
  const t = useTranslations("finops");
  const [filters, setFilters] = useState<FinopsFilterState>({
    project: fixedProject ?? initialProject,
    period: "30d",
    granularity: "daily",
    cloud: "",
  });
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const { projects } = useProjects();
  const { data, loading, error } = useFinopsOverview(
    filters.project || undefined,
    filters.period,
    filters.granularity,
    filters.cloud || undefined,
  );
  const meta = useFinopsMeta();
  const estimates = useFinopsEstimates(filters.project || undefined);
  // Forecast and anomalies are daily figures: drawn on the daily view only.
  const daily = filters.granularity === "daily";
  const anomalies = useFinopsAnomalies(
    filters.project || undefined,
    filters.period,
    daily,
  );

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await downloadFinopsCsv({
        project: filters.project || undefined,
        period: filters.period,
        cloud: filters.cloud || undefined,
      });
    } catch (err) {
      setExportError(
        err instanceof Error ? err.message : t("overview.exportFailed"),
      );
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <FinopsFilters
          state={filters}
          onChange={setFilters}
          projects={projects}
          showProject={!fixedProject}
          showCloud
        />
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={exportCsv}
            disabled={exporting || meta?.provider === "simulated"}
            title={
              meta?.provider === "simulated"
                ? t("overview.exportDisabled")
                : undefined
            }
          >
            <Download className="h-4 w-4" />
            {t("overview.exportCsv")}
          </Button>
          {actions}
        </div>
      </div>

      {exportError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {exportError}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading && !data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
            {[1, 2, 3, 4, 5].map((i) => (
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
            meta={meta}
            savings={data.savings_ytd}
            forecast={data.forecast ?? null}
          />

          <Card>
            <CardHeader>
              <CardTitle>{t("overview.cloudSplit")}</CardTitle>
            </CardHeader>
            <CardContent>
              <CloudSplit
                byCloud={data.by_cloud}
                currency={data.summary.currency}
              />
            </CardContent>
          </Card>

          <CostCharts
            timeline={data.timeline}
            breakdown={data.summary.breakdown}
            forecast={daily ? (data.forecast ?? null) : null}
            granularity={filters.granularity}
            anomalies={anomalies}
            meta={meta}
          />

          <Card>
            <CardHeader>
              <CardTitle>{t("overview.appTracking")}</CardTitle>
            </CardHeader>
            <CardContent>
              <AppCostTable apps={data.apps} />
            </CardContent>
          </Card>

          {estimates.data && estimates.data.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>{t("overview.whereToRun")}</CardTitle>
              </CardHeader>
              <CardContent>
                <CloudEstimateTable rows={estimates.data} />
              </CardContent>
            </Card>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
