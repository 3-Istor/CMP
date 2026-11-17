"use client";

import { CostDonut } from "@/components/finops/CostDonut";
import { CostTimeline } from "@/components/finops/CostTimeline";
import { DataFreshness } from "@/components/finops/DataFreshness";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  CostAnomaly,
  CostBreakdown,
  CostForecast,
  CostSeriesPoint,
  FinopsMeta,
  FinopsResource,
} from "@/types";
import { useTranslations } from "next-intl";
import { useState } from "react";

/** Spend over time and its breakdown; a donut slice focuses the chart. */
export function CostCharts({
  timeline,
  breakdown,
  forecast,
  granularity,
  anomalies,
  meta,
}: {
  timeline: CostSeriesPoint[];
  breakdown: CostBreakdown;
  forecast: CostForecast | null;
  granularity: string;
  anomalies: CostAnomaly[];
  meta: FinopsMeta | null;
}) {
  const t = useTranslations("finops");
  const [selectedResource, setSelectedResource] =
    useState<FinopsResource | null>(null);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader className="flex-row items-center justify-between">
          <div className="space-y-1">
            <CardTitle>{t("overview.costTrend")}</CardTitle>
            <DataFreshness meta={meta} />
          </div>
          {selectedResource && (
            <button
              onClick={() => setSelectedResource(null)}
              className="text-xs text-primary hover:underline"
            >
              {t("overview.resetFilter")}
            </button>
          )}
        </CardHeader>
        <CardContent>
          <CostTimeline
            data={timeline}
            focus={selectedResource}
            forecast={forecast}
            granularity={granularity}
            anomalies={anomalies}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("overview.costBreakdown")}</CardTitle>
          <DataFreshness meta={meta} />
        </CardHeader>
        <CardContent>
          <CostDonut
            breakdown={breakdown}
            selected={selectedResource}
            onSelect={setSelectedResource}
          />
        </CardContent>
      </Card>
    </div>
  );
}
