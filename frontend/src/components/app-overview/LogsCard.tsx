"use client";

import { OpenDashboardButton } from "@/components/ui/open-dashboard-button";
import { getAppLogs } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { useTranslations } from "next-intl";
import { LogList } from "./LogList";
import { OverviewCard } from "./OverviewCard";

export function LogsCard({
  appId,
  base,
  allowed,
}: {
  appId: number;
  base: string;
  allowed: boolean;
}) {
  const t = useTranslations("appPage.logs");
  const logs = useCardData(
    `logs-${appId}`,
    () => getAppLogs(appId, { limit: 20, since: "1h" }),
    { intervalMs: 30_000, enabled: allowed },
  );
  const lines = logs.data ? [...logs.data].reverse() : [];
  return (
    <OverviewCard
      title={t("recent")}
      action={
        allowed ? (
          <OpenDashboardButton href={`${base}/logs`} label={t("open")} />
        ) : undefined
      }
      state={{
        loading: logs.loading,
        error: logs.error,
        forbidden: !allowed || logs.forbidden,
        stale: logs.stale,
        updatedAt: logs.updatedAt,
        hasData: logs.data !== null,
        empty: logs.data !== null && logs.data.length === 0,
        retry: logs.retry,
      }}
      emptyText={t("emptyRecent")}
      skeletonClassName="h-64 w-full"
    >
      <div
        tabIndex={0}
        aria-label={t("region")}
        className="max-h-64 min-h-64 space-y-0.5 overflow-y-auto"
      >
        <LogList lines={lines} />
      </div>
    </OverviewCard>
  );
}
