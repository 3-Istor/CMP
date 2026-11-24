"use client";

import { EmptyState } from "@/components/data/EmptyState";
import { useObsFormat } from "@/components/observability/shared";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import type { SummaryDeploy } from "@/lib/appHealth";
import type { AppSummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { Panel, SkeletonLines, useDuration } from "./shared";

const OUTCOME_TONE: Record<SummaryDeploy["outcome"], string> = {
  live: "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
  replaced: "text-muted-foreground",
  failed: "border-red-600/40 bg-red-600/15 text-red-700 dark:text-red-400",
  running: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  unknown: "text-muted-foreground",
};

export function DeploymentsPanel({
  deploys,
  loading,
  base,
  promotionFor,
  onRollback,
  className,
}: {
  deploys: SummaryDeploy[];
  loading: boolean;
  base: string;
  promotionFor: (version: string) => number | null;
  onRollback: (target: { version: string; promotionId: number }) => void;
  className?: string;
}) {
  const t = useTranslations("appOverview.deployments");
  const { time } = useObsFormat();
  const duration = useDuration();
  return (
    <Panel
      title={t("title")}
      description={t("description")}
      action={
        <Link href={`${base}/delivery`} className="text-sm text-primary hover:underline">
          {t("all")}
        </Link>
      }
      className={className}
    >
      {loading ? (
        <SkeletonLines count={3} className="space-y-4" />
      ) : deploys.length === 0 ? (
        <EmptyState compact title={t("empty")} reason={t("emptyReason")} />
      ) : (
        <ul className="space-y-3">
          {deploys.slice(0, 3).map((d) => {
            const promotionId = d.outcome === "replaced" ? promotionFor(d.version) : null;
            const details = [
              time(d.at),
              d.by,
              d.ready_seconds !== undefined && t("readyIn", { duration: duration(d.ready_seconds) }),
              d.outcome === "replaced" &&
                d.live_seconds !== undefined &&
                t("liveFor", { duration: duration(d.live_seconds) }),
            ].filter(Boolean);
            return (
              <li key={`${d.version}-${d.at}`} className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-sm">
                    <span className="truncate font-mono font-semibold">{d.version}</span>
                    <Badge variant="outline" className={cn("px-1.5 py-0 text-[11px]", OUTCOME_TONE[d.outcome])}>
                      {t(d.outcome)}
                    </Badge>
                  </p>
                  <p className="truncate text-xs text-muted-foreground">{details.join(" · ")}</p>
                </div>
                {d.outcome === "live" || d.outcome === "running" || d.outcome === "failed" ? (
                  <Link href={`${base}/delivery`} className={buttonVariants({ variant: "outline", size: "xs" })}>
                    {t("logs")}
                  </Link>
                ) : promotionId !== null ? (
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => onRollback({ version: d.version, promotionId })}
                  >
                    {t("rollback")}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

export function TopErrorsPanel({ summary, loading, base }: { summary: AppSummary | null; loading: boolean; base: string }) {
  const t = useTranslations("appOverview.topErrors");
  const { number } = useObsFormat();
  const rows = summary?.top_errors ?? [];
  return (
    <Panel title={t("title")} description={t("description")}>
      {loading ? (
        <SkeletonLines count={2} />
      ) : rows.length === 0 ? (
        <EmptyState compact title={t("none")} />
      ) : (
        <ol className="space-y-1.5 text-xs">
          {rows.map((r) => (
            <li key={r.message}>
              <Link
                href={`${base}/logs?q=${encodeURIComponent(r.message.slice(0, 200))}`}
                className="flex items-baseline justify-between gap-3 hover:underline"
              >
                <code className="min-w-0 truncate">{r.message}</code>
                <span className="shrink-0 font-semibold tabular-nums">{number(r.count, 0)}</span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

export function AlertsPanel({
  summary,
  loading,
  alertsHref,
}: {
  summary: AppSummary | null;
  loading: boolean;
  alertsHref: string;
}) {
  const t = useTranslations("appOverview.alerts");
  const firing = summary?.alerts_firing ?? 0;
  return (
    <Panel title={t("title")} description={t("description")}>
      {loading ? (
        <SkeletonLines count={1} />
      ) : firing > 0 ? (
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-semibold text-destructive">{t("firing", { count: firing })}</span>
          <Link href={alertsHref} className={buttonVariants({ variant: "outline", size: "xs" })}>
            {t("open")}
          </Link>
        </div>
      ) : (
        <EmptyState compact title={t("none")} action={{ label: t("seeRules"), href: alertsHref }} />
      )}
    </Panel>
  );
}

export function TalksToPanel({ summary, loading }: { summary: AppSummary | null; loading: boolean }) {
  const t = useTranslations("appOverview.talksTo");
  const clients = summary?.clients ?? [];
  const denied = summary?.denied_flows_24h ?? 0;
  return (
    <Panel title={t("title")} description={t("description")}>
      {loading ? (
        <SkeletonLines count={1} />
      ) : (
        <>
          {clients.length === 0 ? (
            <EmptyState compact title={t("none")} />
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {clients.map((c) => (
                <li
                  key={`${c.app}-${c.verdict}`}
                  className={cn(
                    "rounded-md border px-2 py-0.5 text-xs",
                    c.verdict === "denied" && "border-red-600/40 bg-red-600/10 text-red-700 dark:text-red-400",
                  )}
                >
                  {c.app}
                </li>
              ))}
            </ul>
          )}
          {denied > 0 && (
            <p className="text-xs font-medium text-red-700 dark:text-red-400">{t("denied", { count: denied })}</p>
          )}
        </>
      )}
    </Panel>
  );
}

export function ClientsPanel({
  summary,
  loading,
  className,
}: {
  summary: AppSummary | null;
  loading: boolean;
  className?: string;
}) {
  const t = useTranslations("appOverview.clients");
  const tt = useTranslations("appOverview.talksTo");
  const { number } = useObsFormat();
  const clients = summary?.clients ?? [];
  const denied = summary?.denied_flows_24h ?? 0;
  return (
    <Panel title={t("title")} description={t("description")} className={className}>
      {loading ? (
        <SkeletonLines count={3} />
      ) : clients.length === 0 ? (
        <EmptyState compact title={tt("none")} />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {clients.map((c) => (
            <li key={`${c.app}-${c.verdict}`} className="flex items-center justify-between gap-2">
              <span className="truncate">{c.app}</span>
              <Badge
                variant="outline"
                className={cn(
                  "shrink-0 text-[11px]",
                  c.verdict === "denied"
                    ? "border-red-600/40 bg-red-600/15 text-red-700 dark:text-red-400"
                    : "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
                )}
              >
                {t("verdict", { verdict: c.verdict, flows: number(c.flows, 0) })}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      {!loading && denied > 0 && (
        <p className="text-xs font-medium text-red-700 dark:text-red-400">{tt("denied", { count: denied })}</p>
      )}
    </Panel>
  );
}
