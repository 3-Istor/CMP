"use client";

import { intlLocale, type Translate } from "@/components/finops/format";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ReleaseEvent, ReleaseStep } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import {
  durationBetween,
  formatDateTime,
  isVisibleStep,
  imageTag,
  OUTCOME_TONE,
  outcomeLabel,
  TONE,
} from "./format";

const SEGMENT: Record<ReleaseStep["status"], string> = {
  done: "bg-green-600",
  failed: "bg-red-600",
  active: "bg-amber-500",
  running: "bg-primary",
  skipped: "bg-muted-foreground/30",
  pending: "bg-muted",
};

function Progress({ event, t }: { event: ReleaseEvent; t: Translate }) {
  const steps = (event.steps ?? []).filter(isVisibleStep);
  const reached =
    event.reached_step != null
      ? steps.filter((s) => s.index <= event.reached_step!).length
      : null;
  const label =
    reached != null && steps.length > 0
      ? t("progress.reached", { reached: Math.max(reached, 1), total: steps.length })
      : t("progress.unknown");
  if (steps.length === 0) {
    return <span className="text-xs text-muted-foreground">-</span>;
  }
  return (
    <span className="flex gap-0.5" role="img" aria-label={label} title={label}>
      {steps.map((s) => (
        <i key={s.index} className={cn("h-2 w-2.5 rounded-sm", SEGMENT[s.status])} />
      ))}
    </span>
  );
}

function forcedReason(event: ReleaseEvent): string | null {
  const withReason = (event.actors ?? []).filter((a) => a.reason);
  return withReason.length ? withReason[withReason.length - 1].reason : null;
}

export function ReleaseHistoryTable({ events }: { events: ReleaseEvent[] }) {
  const t = useTranslations("release");
  const locale = intlLocale(useLocale());
  if (events.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{t("history.empty")}</p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">{t("history.columns.revision")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("history.columns.version")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("history.columns.outcome")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("history.columns.progress")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("history.columns.startedAt")}</th>
            <th scope="col" className="py-2 font-medium">{t("history.columns.duration")}</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const reason = e.forced ? forcedReason(e) : null;
            return (
              <tr key={e.id} className="border-b last:border-0 align-top">
                <td className="py-2 pr-4 font-mono">#{e.revision}</td>
                <td className="py-2 pr-4">
                  <span className="font-mono" title={e.image}>
                    {imageTag(e.image)}
                  </span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {t.has(`strategyNames.${e.strategy}`) ? t(`strategyNames.${e.strategy}`) : e.strategy}
                  </span>
                </td>
                <td className="py-2 pr-4">
                  <div className="flex flex-wrap gap-1">
                    <Badge variant="outline" className={TONE[OUTCOME_TONE[e.outcome] ?? "muted"]}>
                      {outcomeLabel(e.outcome, t)}
                    </Badge>
                    {e.forced && (
                      <Badge variant="outline" className={TONE.warn}>
                        {t("history.forced")}
                      </Badge>
                    )}
                  </div>
                  {reason && (
                    <p className="mt-1 max-w-64 text-xs text-muted-foreground">
                      {t("history.reason", { reason })}
                    </p>
                  )}
                </td>
                <td className="py-2 pr-4">
                  <Progress event={e} t={t} />
                </td>
                <td className="py-2 pr-4 font-mono text-xs tabular-nums">
                  {formatDateTime(e.started_at, locale)}
                </td>
                <td className="py-2 font-mono text-xs tabular-nums">
                  {durationBetween(e.started_at, e.finished_at, t) || "-"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
