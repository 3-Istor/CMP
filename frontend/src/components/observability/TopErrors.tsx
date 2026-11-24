"use client";

import { getObsTopErrors } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import type { ErrorPattern } from "@/types/observability";
import { Waypoints } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { CardState, Section, useObsFormat } from "./shared";

export function TopErrors({
  appId,
  base,
  range,
  onPick,
  actions,
}: {
  appId: number;
  base: string;
  range: string;
  onPick?: (row: ErrorPattern) => void;
  actions?: React.ReactNode;
}) {
  const t = useTranslations("obs.topErrors");
  const { time } = useObsFormat();
  const card = useCardData(`obs-top-${appId}-${range}`, () => getObsTopErrors(appId, range, 5));
  return (
    <Section
      title={t("title")}
      description={t("description")}
      actions={
        // With a picker it already sits on the logs page: no link to itself.
        actions ??
        (!onPick && (
          <Link href={`${base}/logs`} className="text-sm text-primary hover:underline">
            {t("seeAll")}
          </Link>
        ))
      }
    >
      <CardState card={card} signal="logs" height="h-28">
        {(rows) =>
          rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <ol className="divide-y rounded-xl border">
              {rows.map((r) => (
                <li key={r.pattern} className="flex flex-col gap-1 p-3 sm:flex-row sm:items-center sm:gap-3">
                  <span className="w-20 shrink-0 text-sm font-semibold tabular-nums text-destructive">
                    {t("count", { count: r.count })}
                  </span>
                  {onPick ? (
                    <button
                      type="button"
                      onClick={() => onPick(r)}
                      className="min-w-0 flex-1 break-all text-left font-mono text-xs hover:underline focus-visible:underline"
                    >
                      {r.pattern}
                    </button>
                  ) : (
                    <code className="min-w-0 flex-1 break-all text-xs">{r.pattern}</code>
                  )}
                  <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                    {r.last_seen && t("lastSeen", { time: time(r.last_seen) })}
                    {r.trace_id && (
                      <Link
                        href={`${base}/traces?trace=${r.trace_id}`}
                        className="inline-flex items-center gap-1 text-primary hover:underline"
                      >
                        <Waypoints className="h-3.5 w-3.5" aria-hidden />
                        {t("openTrace")}
                      </Link>
                    )}
                  </span>
                </li>
              ))}
            </ol>
          )
        }
      </CardState>
    </Section>
  );
}
