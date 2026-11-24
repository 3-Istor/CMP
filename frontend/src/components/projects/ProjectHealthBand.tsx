"use client";

import { Panel, SERIES_COLORS } from "@/components/app-overview/shared";
import { TimeChart, type TimeSeries } from "@/components/data/TimeChart";
import { useObsFormat } from "@/components/observability/shared";
import { peakOf, toPoints } from "@/lib/appHealth";
import type { ProjectAppsHealth } from "@/lib/api";
import { useTranslations } from "next-intl";

function byAppSeries(byApp: Record<string, (number | null)[]>, type: TimeSeries["type"], stack?: string): TimeSeries[] {
  return Object.keys(byApp)
    .sort()
    .map((app, i) => ({
      key: app,
      label: app,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      type,
      stack,
    }));
}

const nonZero = (byApp: Record<string, (number | null)[]>) =>
  Object.fromEntries(Object.entries(byApp).filter(([, values]) => values.some((v) => v != null && v > 0)));

/** Three charts by app: a curve that leaves the pack names the app and the moment. */
export function ProjectHealthBand({ data, loading }: { data: ProjectAppsHealth | null; loading: boolean }) {
  const t = useTranslations("projectHome.band");
  const { number, percent, locale } = useObsFormat();
  const series = data?.series;
  const rps = nonZero(series?.rps_by_app ?? {});
  const errors = nonZero(series?.err_pct_by_app ?? {});
  const peak = series ? peakOf(series.t, errors) : null;
  const errorLogs = series?.error_logs_per_min ?? [];
  const empty = { title: t("empty"), reason: t("emptyReason") };
  const clock = (ms: number) => new Date(ms).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel title={t("requests")} description={t("requestsSub")}>
        <TimeChart
          label={t("requests")}
          loading={loading}
          height={130}
          data={series && Object.keys(rps).length ? toPoints(series.t, rps) : []}
          series={byAppSeries(rps, "area", "rps")}
          yFormat={(v) => number(v, 2)}
          empty={empty}
        />
      </Panel>
      <Panel title={t("errors")} description={t("errorsSub")}>
        <TimeChart
          label={t("errors")}
          loading={loading}
          height={130}
          data={series && Object.keys(errors).length ? toPoints(series.t, errors) : []}
          series={byAppSeries(errors, "line")}
          yFormat={(v) => percent(v / 100, 1)}
          empty={empty}
        />
        {peak && (
          <p className="text-xs font-medium text-red-700 dark:text-red-400">
            {t("peak", { app: peak.key, value: percent(peak.value / 100, 1), time: clock(peak.t) })}
          </p>
        )}
      </Panel>
      <Panel title={t("errorLogs")} description={t("errorLogsSub")}>
        <TimeChart
          label={t("errorLogs")}
          loading={loading}
          height={130}
          data={series && errorLogs.some((v) => v != null && v > 0) ? toPoints(series.t, { logs: errorLogs }) : []}
          series={[{ key: "logs", label: t("allApps"), color: "var(--destructive)", type: "bar" }]}
          yFormat={(v) => number(v, 1)}
          legend
          empty={empty}
        />
      </Panel>
    </div>
  );
}
