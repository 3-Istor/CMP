"use client";

import { StatTile } from "@/components/data/StatTile";
import { useObsCapabilities, useObsFormat } from "@/components/observability/shared";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { getFinopsApps, getObsDora, getObsSlo, type AppEnv } from "@/lib/api";
import { useInView } from "@/lib/appSummary";
import { loadScorecard } from "@/lib/scorecard";
import { useCardData } from "@/lib/useCardData";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ScoreCard } from "./ScoreCard";

/** The slow-moving signals, under the fold: fetched only once scrolled near. */
export function WeeklyView({
  appId,
  projectName,
  base,
  env,
  repoUrl,
  canViewObservability,
  canViewFinops,
  hasScore,
}: {
  appId: number;
  projectName: string;
  base: string;
  env: AppEnv;
  repoUrl: string | null;
  canViewObservability: boolean;
  canViewFinops: boolean;
  hasScore: boolean;
}) {
  const t = useTranslations("appOverview.weekly");
  const tOv = useTranslations("appOverview");
  // No SLO is computed for staging yet.
  const staging = env === "staging";
  const { number, percent, euro } = useObsFormat();
  const [ref, seen] = useInView<HTMLDivElement>();
  const [scoreOpen, setScoreOpen] = useState(false);
  const caps = useObsCapabilities(appId, seen && canViewObservability).data;
  const slo = useCardData(`weekly-slo-${appId}`, () => getObsSlo(appId, "7d"), {
    enabled: seen && Boolean(caps?.slo) && !staging,
  });
  const dora = useCardData(`weekly-dora-${appId}-${env}`, () => getObsDora(appId, 30, env), {
    enabled: seen && Boolean(caps?.metrics),
  });
  const score = useCardData(`scorecard-${appId}`, () => loadScorecard(appId), {
    enabled: seen && hasScore,
  });
  const costs = useCardData(`weekly-cost-${projectName}-${env}`, () => getFinopsApps(projectName, env), {
    enabled: seen && canViewFinops,
  });

  const capsLoading = canViewObservability && !caps;
  const availability = staging ? undefined : slo.data?.objectives.find((o) => o.kind === "availability");
  const budget = availability?.budget_remaining;
  const reliabilityHint = !caps?.slo
    ? t("unavailable")
    : !availability
      ? t("reliabilityNoTargetHint")
      : budget == null
        ? t("reliabilityNoData")
        : t("reliabilityBudget", { pct: percent(Math.max(0, budget), 0) });

  const d = dora.data;
  const deliveryHint = !caps?.metrics
    ? t("unavailable")
    : d && d.deployments + d.failed_changes === 0
      ? t("deliveryNone")
      : d
        ? t("deliveryHint", { failed: d.failed_changes, perWeek: number(d.deploys_per_week, 1) })
        : undefined;

  const card = score.data;
  const toImprove = card ? card.criteria.reduce((n, c) => n + c.actions.length, 0) : 0;
  const costRow = costs.data?.find((r) => r.app_id === appId);

  return (
    <div ref={ref}>
      <div className="my-2 flex items-center gap-2 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        {t("title")}
        <span className="h-px flex-1 bg-border" />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label={t("reliability")}
          href={`${base}/settings`}
          loading={!seen || (!staging && (capsLoading || slo.loading))}
          value={availability ? percent(availability.target / 100, 2) : caps?.slo && !staging ? t("reliabilityNoTarget") : null}
          tone={budget != null && budget <= 0 ? "bad" : budget != null && budget < 0.25 ? "warn" : undefined}
          hint={staging ? tOv("notMeasuredStaging") : reliabilityHint}
        />
        <StatTile
          label={t("delivery")}
          href={`${base}/delivery`}
          loading={!seen || capsLoading || dora.loading}
          value={d ? number(d.deployments, 0) : null}
          unit={d ? t("deploys", { count: d.deployments }) : undefined}
          hint={deliveryHint}
        />
        <button
          type="button"
          className="min-w-0 rounded-xl text-left disabled:cursor-default"
          onClick={() => setScoreOpen(true)}
          disabled={!card}
        >
          <StatTile
            label={t("score")}
            className="h-full transition-colors hover:bg-muted/50"
            loading={!seen || (hasScore && score.loading)}
            value={card ? card.grade : null}
            unit={card ? `${card.score}/${card.max}` : undefined}
            hint={card ? t("scoreHint", { count: toImprove }) : t("scoreNone")}
          />
        </button>
        <StatTile
          label={t("cost")}
          href={canViewFinops ? `${base}/costs` : undefined}
          loading={!seen || (canViewFinops && costs.loading)}
          value={costRow ? euro(costRow.month_to_date_eur) : null}
          hint={
            !canViewFinops
              ? t("unavailable")
              : costRow
                ? t("costHint", { estimate: euro(costRow.cost_month_estimate_eur) })
                : t("costNone")
          }
        />
      </div>
      {hasScore && (
        <Dialog open={scoreOpen} onOpenChange={setScoreOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogTitle className="sr-only">{t("score")}</DialogTitle>
            <ScoreCard appId={appId} projectName={projectName} repoUrl={repoUrl} allowed />
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
