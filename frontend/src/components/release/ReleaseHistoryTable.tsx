"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
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

export function ReleaseHistoryTable({
  events,
  loading = false,
}: {
  events: ReleaseEvent[];
  loading?: boolean;
}) {
  const t = useTranslations("release");
  const locale = intlLocale(useLocale());
  const columns: DataColumn<ReleaseEvent>[] = [
    {
      key: "revision",
      header: t("history.columns.revision"),
      sortable: true,
      className: "align-top font-mono",
      cell: (e) => `#${e.revision}`,
    },
    {
      key: "version",
      header: t("history.columns.version"),
      className: "align-top",
      cell: (e) => (
        <>
          <span className="font-mono" title={e.image}>
            {imageTag(e.image)}
          </span>
          <span className="ml-2 text-xs text-muted-foreground">
            {t.has(`strategyNames.${e.strategy}`) ? t(`strategyNames.${e.strategy}`) : e.strategy}
          </span>
        </>
      ),
    },
    {
      key: "outcome",
      header: t("history.columns.outcome"),
      className: "align-top",
      cell: (e) => {
        const reason = e.forced ? forcedReason(e) : null;
        return (
          <>
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
          </>
        );
      },
    },
    {
      key: "progress",
      header: t("history.columns.progress"),
      className: "align-top",
      cell: (e) => <Progress event={e} t={t} />,
    },
    {
      key: "started_at",
      header: t("history.columns.startedAt"),
      sortable: true,
      className: "align-top font-mono text-xs tabular-nums",
      cell: (e) => formatDateTime(e.started_at, locale),
    },
    {
      key: "duration",
      header: t("history.columns.duration"),
      className: "align-top font-mono text-xs tabular-nums",
      cell: (e) => durationBetween(e.started_at, e.finished_at, t) || "-",
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={events}
      rowKey={(e) => e.id}
      loading={loading}
      storageKey="release.history"
      initialSort={{ key: "revision", direction: "desc" }}
      empty={{ title: t("history.empty") }}
    />
  );
}
