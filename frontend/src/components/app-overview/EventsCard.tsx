"use client";

import { getAppEvents, type AppEnv } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import { useLocale, useTranslations } from "next-intl";
import { relativeAge } from "./format";
import { OverviewCard } from "./OverviewCard";

export function EventsCard({
  appId,
  allowed,
  env = "prod",
}: {
  appId: number;
  allowed: boolean;
  env?: AppEnv;
}) {
  const t = useTranslations("appPage.events");
  const locale = useLocale();
  const events = useCardData(`events-${appId}-${env}`, () => getAppEvents(appId, 10, env), {
    intervalMs: 30_000,
    enabled: allowed,
  });
  return (
    <OverviewCard
      title={t("title")}
      state={{
        loading: events.loading,
        error: events.error,
        forbidden: !allowed || events.forbidden,
        stale: events.stale,
        updatedAt: events.updatedAt,
        hasData: events.data !== null,
        empty: events.data !== null && events.data.length === 0,
        retry: events.retry,
      }}
      emptyText={t("empty")}
      skeletonClassName="h-48 w-full"
    >
      <ul className="divide-y">
        {(events.data ?? []).map((e, i) => (
          <li
            key={`${e.time}-${e.object}-${e.reason}-${i}`}
            className="grid grid-cols-[auto_1fr_auto] gap-x-3 py-2 text-sm first:pt-0 last:pb-0"
          >
            <span
              className={cn(
                "font-medium",
                e.type === "Warning" && "text-amber-700 dark:text-amber-400",
              )}
            >
              {e.type === "Warning" ? t("warning") : t("normal")} {e.reason}
            </span>
            <span className="break-words text-muted-foreground">
              {e.message}
              {e.count > 1 && ` (${t("count", { count: e.count })})`}
            </span>
            <span className="whitespace-nowrap text-xs text-muted-foreground">
              {relativeAge(e.time, locale)}
            </span>
          </li>
        ))}
      </ul>
    </OverviewCard>
  );
}
