"use client";

import { imageTag } from "@/components/release/format";
import { Badge } from "@/components/ui/badge";
import { OpenDashboardButton } from "@/components/ui/open-dashboard-button";
import { getReleaseHistory } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import type { ReleaseEvent } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import { dateTime, duration } from "./format";
import { OverviewCard } from "./OverviewCard";

const OUTCOME_TONE: Record<string, string> = {
  promoted: "border-green-600/40 text-green-700 dark:text-green-400",
  running: "border-sky-500/40 text-sky-700 dark:text-sky-300",
  aborted_auto: "border-red-600/40 text-red-700 dark:text-red-400",
  aborted_manual: "border-red-600/40 text-red-700 dark:text-red-400",
  superseded: "text-muted-foreground",
};

export function DeploymentsTable({ events }: { events: ReleaseEvent[] }) {
  const t = useTranslations("appPage.deployments");
  const locale = useLocale();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">{t("version")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("outcome")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("who")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("when")}</th>
            <th scope="col" className="py-2 font-medium">{t("duration")}</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const actor = (e.actors ?? []).at(-1)?.user;
            return (
              <tr key={e.id} className="border-b align-top last:border-0">
                <td className="py-2 pr-4 font-mono" title={e.image}>
                  {imageTag(e.image)}
                </td>
                <td className="py-2 pr-4">
                  <Badge variant="outline" className={OUTCOME_TONE[e.outcome]}>
                    {t.has(`outcomes.${e.outcome}`)
                      ? t(`outcomes.${e.outcome}`)
                      : e.outcome}
                  </Badge>
                  {e.forced && (
                    <Badge variant="outline" className="ml-1">
                      {t("forced")}
                    </Badge>
                  )}
                </td>
                <td className="py-2 pr-4">{actor ?? t("automatic")}</td>
                <td className="py-2 pr-4 font-mono text-xs tabular-nums">
                  {dateTime(e.started_at, locale)}
                </td>
                <td className="py-2 font-mono text-xs tabular-nums">
                  {duration(e.started_at, e.finished_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DeploymentsCard({
  appId,
  base,
  canOpen,
}: {
  appId: number;
  base: string;
  canOpen: boolean;
}) {
  const t = useTranslations("appPage.deployments");
  const history = useCardData(
    `deployments-${appId}`,
    () => getReleaseHistory(appId),
    { intervalMs: 30_000 },
  );
  return (
    <OverviewCard
      title={t("recent")}
      action={
        canOpen ? (
          <OpenDashboardButton href={`${base}/deployments`} label={t("open")} />
        ) : undefined
      }
      state={{
        loading: history.loading,
        error: history.error,
        forbidden: history.forbidden,
        stale: history.stale,
        updatedAt: history.updatedAt,
        hasData: history.data !== null,
        empty: history.data !== null && history.data.length === 0,
        retry: history.retry,
      }}
      emptyText={t("empty")}
      skeletonClassName="h-48 w-full"
    >
      <DeploymentsTable events={(history.data ?? []).slice(0, 5)} />
    </OverviewCard>
  );
}
