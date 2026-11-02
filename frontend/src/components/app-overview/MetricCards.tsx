"use client";

import { intlLocale, formatEUR } from "@/components/finops/format";
import { buttonVariants } from "@/components/ui/button";
import { getAppMetrics, getFinopsApps } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { MetricSeries, ProjectLink } from "@/types";
import { ExternalLink } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { OverviewCard, type CardState } from "./OverviewCard";
import { Sparkline } from "./Sparkline";

const REFRESH_MS = 60_000;

function GrafanaLink({ links }: { links: ProjectLink[] }) {
  const t = useTranslations("appPage.common");
  const link = links.find((l) => l.id.startsWith("grafana") && l.app);
  if (!link) return null;
  return (
    <a
      href={link.url}
      target="_blank"
      rel="noopener noreferrer"
      title={t("openGrafana")}
      aria-label={t("openGrafana")}
      className={cn(
        buttonVariants({ variant: "ghost", size: "icon-sm" }),
        "text-muted-foreground hover:text-foreground",
      )}
    >
      <ExternalLink aria-hidden />
    </a>
  );
}

function Value({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-2xl font-semibold tabular-nums leading-none">
      {children}
    </p>
  );
}

function SeriesBody({
  series,
  format,
  label,
  note,
}: {
  series: MetricSeries;
  format: (value: number) => string;
  label: string;
  note?: React.ReactNode;
}) {
  const values = series.points.map((p) => p.value);
  const summary = `${label}, min ${format(Math.min(...values))}, max ${format(Math.max(...values))}`;
  return (
    <div className="space-y-2">
      <Value>{format(series.current ?? 0)}</Value>
      <Sparkline points={series.points} label={summary} />
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

export function MetricCards({
  appId,
  links,
  isKubernetes,
  allowed,
}: {
  appId: number;
  links: ProjectLink[];
  isKubernetes: boolean;
  allowed: boolean;
}) {
  const t = useTranslations("appPage.metrics");
  const locale = useLocale();
  const numbers = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const metrics = useCardData(
    `metrics-${appId}`,
    () => getAppMetrics(appId, "24h"),
    { intervalMs: REFRESH_MS, enabled: isKubernetes && allowed },
  );
  const m = metrics.data;
  const state = (series: MetricSeries | null | undefined): CardState => ({
    loading: metrics.loading,
    error: metrics.error,
    forbidden: !allowed || metrics.forbidden,
    stale: metrics.stale,
    updatedAt: metrics.updatedAt,
    hasData: m !== null,
    empty: m !== null && !series,
    retry: metrics.retry,
  });
  const action = <GrafanaLink links={links} />;
  const skeleton = "h-24 w-full";

  return (
    <>
      <OverviewCard
        title={t("requests")}
        action={action}
        state={state(m?.requests_per_second)}
        emptyText={t("noTraffic")}
        skeletonClassName={skeleton}
      >
        {m?.requests_per_second && (
          <SeriesBody
            series={m.requests_per_second}
            label={t("requests")}
            format={(v) => `${numbers.format(v)} ${t("perSecond")}`}
            note={t("last24h")}
          />
        )}
      </OverviewCard>
      <OverviewCard
        title={t("errors")}
        action={action}
        state={state(m?.errors_5xx_percent)}
        emptyText={t("noTraffic")}
        skeletonClassName={skeleton}
      >
        {m?.errors_5xx_percent && (
          <SeriesBody
            series={m.errors_5xx_percent}
            label={t("errors")}
            format={(v) => `${numbers.format(v)} %`}
            note={t("last24h")}
          />
        )}
      </OverviewCard>
      <OverviewCard
        title={t("cpu")}
        action={action}
        state={state(m?.cpu_millicores)}
        emptyText={t("noData")}
        skeletonClassName={skeleton}
      >
        {m?.cpu_millicores && (
          <SeriesBody
            series={m.cpu_millicores}
            label={t("cpu")}
            format={(v) => `${numbers.format(Math.round(v))} m`}
            note={
              m.cpu_request_millicores !== null
                ? t("cpuRequest", {
                    value: numbers.format(Math.round(m.cpu_request_millicores)),
                  })
                : t("last24h")
            }
          />
        )}
      </OverviewCard>
    </>
  );
}

export function CostCard({
  appId,
  projectName,
  allowed,
}: {
  appId: number;
  projectName: string;
  allowed: boolean;
}) {
  const t = useTranslations("appPage.metrics");
  const locale = intlLocale(useLocale());
  const costs = useCardData(
    `cost-${appId}`,
    () => getFinopsApps(projectName),
    { intervalMs: 5 * REFRESH_MS, enabled: allowed },
  );
  const row = costs.data?.find((r) => r.app_id === appId);
  return (
    <OverviewCard
      title={t("cost")}
      state={{
        loading: costs.loading,
        error: costs.error,
        forbidden: !allowed || costs.forbidden,
        stale: costs.stale,
        updatedAt: costs.updatedAt,
        hasData: costs.data !== null,
        empty: costs.data !== null && !row,
        retry: costs.retry,
      }}
      emptyText={t("noCost")}
      skeletonClassName="h-24 w-full"
    >
      {row && (
        <div className="space-y-2">
          <Value>{formatEUR(row.month_to_date_eur, "EUR", locale)}</Value>
          <div className="h-9" aria-hidden />
          <p className="text-xs text-muted-foreground">
            {t("costNote", {
              estimate: formatEUR(row.cost_month_estimate_eur, "EUR", locale),
            })}
          </p>
        </div>
      )}
    </OverviewCard>
  );
}
