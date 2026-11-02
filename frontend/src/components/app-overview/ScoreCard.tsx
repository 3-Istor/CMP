"use client";

import { ScoreBadge } from "@/components/scorecard/ScoreBadge";
import { actionHref, loadScorecard } from "@/lib/scorecard";
import { useCardData } from "@/lib/useCardData";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { OverviewCard } from "./OverviewCard";

export function ScoreCard({
  appId,
  projectName,
  repoUrl,
  allowed,
}: {
  appId: number;
  projectName: string;
  repoUrl: string | null;
  allowed: boolean;
}) {
  const t = useTranslations("scorecard");
  const { data, loading, error, forbidden, retry } = useCardData(
    `scorecard-${appId}`,
    () => loadScorecard(appId),
    { enabled: allowed },
  );

  return (
    <OverviewCard
      title={t("title")}
      action={<ScoreBadge card={data ?? undefined} />}
      state={{
        loading,
        error,
        forbidden: forbidden || !allowed,
        hasData: data !== null,
        retry,
      }}
      emptyText={t("title")}
      skeletonClassName="h-40 w-full"
    >
      <ul className="divide-y">
        {(data?.criteria ?? []).map((criterion) => (
          <li key={criterion.key} className="space-y-1 py-2 first:pt-0 last:pb-0">
            <div className="flex items-baseline justify-between gap-4 text-sm">
              <span className="font-medium">
                {t(`criteria.${criterion.key}`)}
              </span>
              <span className="font-mono tabular-nums">
                {criterion.points}/{criterion.max}
              </span>
            </div>
            {criterion.reasons.length > 0 && (
              <ul className="text-xs text-muted-foreground">
                {criterion.reasons.map((reason) => (
                  <li key={reason}>
                    {t.has(`reasons.${reason}`) ? t(`reasons.${reason}`) : reason}
                  </li>
                ))}
              </ul>
            )}
            {criterion.actions.length > 0 && (
              <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                {criterion.actions.map((action) => {
                  const href = actionHref(action, projectName, appId, repoUrl);
                  const label = t.has(`actions.${action}`)
                    ? t(`actions.${action}`)
                    : action;
                  return (
                    <li key={action}>
                      {href === null ? (
                        <span>{label}</span>
                      ) : href.startsWith("http") ? (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary underline"
                        >
                          {label}
                        </a>
                      ) : (
                        <Link href={href} className="text-primary underline">
                          {label}
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </OverviewCard>
  );
}
