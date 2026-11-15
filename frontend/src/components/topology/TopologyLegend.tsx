"use client";

import { Info } from "lucide-react";
import { useTranslations } from "next-intl";

import { edgeVisual, type GraphEdgeState } from "@/lib/topology";

const LIVE: GraphEdgeState[] = ["declared_observed", "declared_only", "observed_only", "denied"];

export function EdgeSample({ state }: { state: GraphEdgeState }) {
  const visual = edgeVisual(state);
  return (
    <svg width="36" height="10" aria-hidden className="shrink-0">
      <line
        x1="2"
        y1="5"
        x2="34"
        y2="5"
        stroke={visual.stroke}
        strokeWidth={visual.width}
        strokeDasharray={visual.dash}
      />
    </svg>
  );
}

export function TopologyLegend({
  draft = false,
  observedAvailable = true,
}: {
  draft?: boolean;
  observedAvailable?: boolean;
}) {
  const t = useTranslations("topology");
  const states = draft ? [...LIVE, "draft" as const] : LIVE;
  return (
    <div className="space-y-2">
      <ul aria-label={t("legend")} className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-muted-foreground">
        {states.map((state) => (
          <li key={state} className="flex items-center gap-1.5">
            <EdgeSample state={state} />
            {t(`edgeState.${state}`)}
          </li>
        ))}
      </ul>
      {!observedAvailable && (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("observedUnavailable")}
        </p>
      )}
    </div>
  );
}
