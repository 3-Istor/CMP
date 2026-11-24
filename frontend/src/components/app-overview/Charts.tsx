"use client";

import { TimeChart } from "@/components/data/TimeChart";
import { useObsFormat } from "@/components/observability/shared";
import { Button } from "@/components/ui/button";
import { toPoints } from "@/lib/appHealth";
import type { AppSummary, SummaryRange } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { Panel } from "./shared";

const RANGES: SummaryRange[] = ["1h", "6h", "24h", "7d"];

export function RangeButtons({
  value,
  onChange,
  label,
}: {
  value: SummaryRange;
  onChange: (range: SummaryRange) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border p-0.5">
      {RANGES.map((range) => (
        <Button
          key={range}
          type="button"
          size="xs"
          variant={range === value ? "secondary" : "ghost"}
          aria-pressed={range === value}
          onClick={() => onChange(range)}
          className={cn("h-6 px-2 text-xs", range !== value && "text-muted-foreground")}
        >
          {range}
        </Button>
      ))}
    </div>
  );
}

function markersOf(summary: AppSummary | null) {
  return (summary?.deploys ?? []).map((d) => ({
    t: Date.parse(d.at),
    label: d.version,
    failed: d.outcome === "failed",
  }));
}

export function TrafficCard({
  summary,
  loading,
  range,
  onRange,
  className,
}: {
  summary: AppSummary | null;
  loading: boolean;
  range: SummaryRange;
  onRange: (range: SummaryRange) => void;
  className?: string;
}) {
  const t = useTranslations("appOverview.traffic");
  const { number, ms } = useObsFormat();
  const series = summary?.series;
  const hasTraffic = (series?.rps ?? []).some((v) => v != null && v > 0);
  const data = series && hasTraffic ? toPoints(series.t, { rps: series.rps, err: series.err_rps, p95: series.p95_ms }) : [];
  return (
    <Panel
      title={t("title")}
      description={t("description")}
      action={<RangeButtons value={range} onChange={onRange} label={t("range")} />}
      className={className}
    >
      <TimeChart
        label={t("title")}
        loading={loading}
        height={180}
        data={data}
        deployMarkers={markersOf(summary)}
        yFormat={(v) => number(v, 2)}
        yFormatRight={ms}
        legend
        empty={{ title: t("noTraffic"), reason: t("noTrafficReason") }}
        series={[
          { key: "rps", label: t("requests"), color: "var(--chart-1)", type: "area" },
          { key: "err", label: t("errors"), color: "var(--destructive)", type: "bar" },
          { key: "p95", label: t("p95"), color: "var(--chart-4)", type: "line", axis: "right", dashed: true },
        ]}
      />
    </Panel>
  );
}

export function ResourcesCard({
  summary,
  loading,
  range,
  onRange,
  className,
}: {
  summary: AppSummary | null;
  loading: boolean;
  range: SummaryRange;
  onRange: (range: SummaryRange) => void;
  className?: string;
}) {
  const t = useTranslations("appOverview.resources");
  const tt = useTranslations("appOverview.traffic");
  const { percent } = useObsFormat();
  const tiles = summary?.tiles;
  const series = summary?.series;
  const ratio = (values: (number | null)[] | undefined, of: number | undefined) =>
    of ? (values ?? []).map((v) => (v == null ? null : v / of)) : undefined;
  const cpu = ratio(series?.cpu_cores, tiles?.cpu_request_cores);
  const mem = ratio(series?.mem_bytes, tiles?.mem_limit_bytes);
  const data =
    series && (cpu || mem)
      ? toPoints(series.t, { mem, cpu, line: series.t.map(() => 1) })
      : [];
  return (
    <Panel
      title={t("title")}
      description={t("description")}
      action={<RangeButtons value={range} onChange={onRange} label={tt("range")} />}
      className={className}
    >
      <TimeChart
        label={t("title")}
        loading={loading}
        height={170}
        data={data}
        deployMarkers={markersOf(summary)}
        yFormat={(v) => percent(v, 0)}
        legend
        empty={{ title: t("empty"), reason: t("emptyReason") }}
        series={[
          { key: "mem", label: t("memory"), color: "var(--chart-1)", type: "area" },
          { key: "cpu", label: t("cpu"), color: "var(--chart-2)", type: "line" },
          { key: "line", label: t("threshold"), color: "var(--muted-foreground)", type: "line", dashed: true },
        ]}
      />
    </Panel>
  );
}

export function LogVolumeCard({
  summary,
  loading,
  className,
}: {
  summary: AppSummary | null;
  loading: boolean;
  className?: string;
}) {
  const t = useTranslations("appOverview.logVolume");
  const { number } = useObsFormat();
  const series = summary?.series;
  const any = series && [...series.log_info, ...series.log_warn, ...series.log_error].some((v) => v != null && v > 0);
  const data = series && any ? toPoints(series.t, { info: series.log_info, warn: series.log_warn, error: series.log_error }) : [];
  return (
    <Panel title={t("title")} description={t("description")} className={className}>
      <TimeChart
        label={t("title")}
        loading={loading}
        height={200}
        data={data}
        yFormat={(v) => number(v, 0)}
        legend
        empty={{ title: t("empty"), reason: t("emptyReason") }}
        series={[
          { key: "info", label: t("info"), color: "var(--chart-2)", type: "bar", stack: "logs" },
          { key: "warn", label: t("warn"), color: "var(--chart-4)", type: "bar", stack: "logs" },
          { key: "error", label: t("error"), color: "var(--destructive)", type: "bar", stack: "logs" },
        ]}
      />
    </Panel>
  );
}
