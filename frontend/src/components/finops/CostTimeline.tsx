"use client";

import type {
  CostAnomaly,
  CostForecast,
  CostSeriesPoint,
  FinopsResource,
} from "@/types";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  RESOURCE_META,
  axisTickFormatter,
  formatEUR,
  intlLocale,
  visibleResources,
} from "./format";

const FORECAST_COLOR = "#64748B";
const ANOMALY_COLOR = "#EF4444";

type Granularity = "daily" | "weekly" | "monthly";

type PartialBucket = {
  date: string;
  partial: number;
  /** Share of the bucket already elapsed, 0 to 1. */
  elapsed: number;
  projected: number;
  stack?: Partial<Record<FinopsResource, [number, number]>>;
};

type StackedRow = TimelineRow & {
  stack?: Partial<Record<FinopsResource, [number, number]>>;
};

type TimelineRow = Partial<CostSeriesPoint> & {
  date: string;
  forecast?: number;
  band?: [number, number];
  anomalies?: CostAnomaly[];
  projection?: number;
  partialBucket?: PartialBucket;
  partialStack?: Partial<Record<FinopsResource, [number, number]>>;
};

const DAY_MS = 86_400_000;

/** Cost data are UTC days: the elapsed share comes from the UTC clock, never the local or Paris hour. */
function utcElapsed(
  date: string,
  granularity: Granularity,
  now: Date,
): number | null {
  const start = Date.parse(`${date}T00:00:00Z`);
  const end =
    granularity === "daily"
      ? start + DAY_MS
      : granularity === "weekly"
        ? start + 7 * DAY_MS
        : Date.UTC(
            new Date(start).getUTCFullYear(),
            new Date(start).getUTCMonth() + 1,
            1,
          );
  const share = (now.getTime() - start) / (end - start);
  return share > 0 && share < 1 ? share : null;
}

function findPartialBucket(
  data: CostSeriesPoint[],
  forecast: CostForecast | null,
  granularity: Granularity,
  now: Date,
): PartialBucket | null {
  const last = data[data.length - 1];
  if (!last) return null;
  const elapsed =
    last.is_partial === false
      ? null
      : (last.elapsed_fraction ?? utcElapsed(last.date, granularity, now));
  if (elapsed === null || elapsed <= 0 || elapsed >= 1) return null;
  const previous = data[data.length - 2];
  const forecastToday = forecast?.daily.find((d) => d.date === last.date);
  // Ingestion runs hourly: past the first 3 h of a day (or any later share of a
  // longer bucket) the bucket's own cost is the best guess; before that, the
  // forecast, then the previous bucket, stand in.
  const reliable = granularity === "daily" ? elapsed >= 0.125 : elapsed >= 0.02;
  const projected = reliable
    ? last.total / elapsed
    : (forecastToday?.eur ?? previous?.total ?? last.total);
  return {
    date: last.date,
    partial: last.total,
    elapsed,
    projected: Math.max(projected, last.total),
  };
}

function CostTooltip({
  active,
  payload,
  label,
  resources,
}: {
  resources: { key: FinopsResource; name: string; color: string }[];
  active?: boolean;
  payload?: Array<{
    name: string;
    value: number;
    color: string;
    dataKey?: string;
    payload?: TimelineRow;
  }>;
  label?: string;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const money = (value: number) => formatEUR(value, "EUR", locale);
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  const costs = resources
    .filter((r) => row?.[r.key] !== undefined)
    .map((r) => ({ name: r.name, color: r.color, value: row![r.key] as number }));
  const total = costs.reduce((s, p) => s + p.value, 0);
  return (
    <div className="max-w-xs rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-medium">
        {label}
        {row?.partialBucket && (
          <span className="ml-1.5 font-normal text-muted-foreground">
            {t("timeline.inProgress")}
          </span>
        )}
      </p>
      {row?.partialBucket && (
        <p className="tabular-nums">
          {t("timeline.soFarDetail", {
            partial: money(row.partialBucket.partial),
            pct: Math.round(row.partialBucket.elapsed * 100),
            projected: money(row.partialBucket.projected),
          })}
        </p>
      )}
      {costs.map((p) => (
        <p key={p.name} className="flex items-center gap-1.5">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: p.color }}
          />
          {p.name}
          <span className="ml-auto tabular-nums">{money(p.value)}</span>
        </p>
      ))}
      {costs.length > 0 && (
        <p className="mt-1 flex justify-between border-t pt-1 font-medium">
          <span>{t("common.total")}</span>
          <span className="tabular-nums">{money(total)}</span>
        </p>
      )}
      {row?.band && row.total === undefined && !row.partialBucket && (
        <p className="flex justify-between gap-3">
          <span>{t("timeline.forecast")}</span>
          <span className="tabular-nums">
            {t("timeline.forecastValue", {
              value: money(row.forecast ?? 0),
              low: money(row.band[0]),
              high: money(row.band[1]),
            })}
          </span>
        </p>
      )}
      {row?.anomalies?.map((a) => (
        <div
          key={a.app_id}
          className="mt-1 space-y-0.5 border-t pt-1"
          style={{ color: ANOMALY_COLOR }}
        >
          <p className="font-medium">
            {t("timeline.anomalyDetail", {
              app: a.app_name,
              cost: money(a.cost_eur),
              baseline: money(a.baseline_eur),
            })}
          </p>
          <p className="text-muted-foreground">
            {t("timeline.cause", { cause: a.cause })}
          </p>
        </div>
      ))}
    </div>
  );
}

function stackOf(
  row: Partial<CostSeriesPoint>,
  resources: FinopsResource[],
  isHidden: (r: FinopsResource) => boolean,
): NonNullable<TimelineRow["partialStack"]> {
  let low = 0;
  const stack: NonNullable<TimelineRow["partialStack"]> = {};
  for (const r of resources) {
    if (isHidden(r)) continue;
    const high = low + ((row[r] as number | undefined) ?? 0);
    stack[r] = [low, high];
    low = high;
  }
  return stack;
}

function buildRows(
  data: CostSeriesPoint[],
  forecast: CostForecast | null,
  anomalies: CostAnomaly[],
  partialBucket: PartialBucket | null,
  resources: FinopsResource[],
  isHidden: (r: FinopsResource) => boolean,
): TimelineRow[] {
  const complete = partialBucket
    ? data.filter((p) => p.date !== partialBucket.date)
    : data;
  const partialPoint = partialBucket
    ? data.find((p) => p.date === partialBucket.date)
    : undefined;
  const rows = new Map<string, TimelineRow>(
    complete.map((p) => [p.date, { ...p }]),
  );
  const anchors: { date: string; total: number }[] = complete.map((p) => ({
    date: p.date,
    total: p.total,
  }));
  if (partialBucket) {
    const lastComplete = complete.findLast((p) => p.date < partialBucket.date);
    if (lastComplete) {
      const row = rows.get(lastComplete.date)!;
      row.projection = lastComplete.total;
      row.partialStack = stackOf(row, resources, isHidden);
    }
    rows.set(partialBucket.date, {
      date: partialBucket.date,
      projection: partialBucket.projected,
      partialStack: partialPoint && stackOf(partialPoint, resources, isHidden),
      partialBucket,
    });
    anchors.push({ date: partialBucket.date, total: partialBucket.projected });
  }
  if (forecast && forecast.daily.length > 0) {
    // Start the dashed line on the last real point so the two connect.
    const lastReal = anchors.findLast((p) => p.date < forecast.daily[0].date);
    if (lastReal) {
      const row = rows.get(lastReal.date)!;
      row.forecast = lastReal.total;
      row.band = [lastReal.total, lastReal.total];
    }
    for (const day of forecast.daily) {
      const row = rows.get(day.date) ?? { date: day.date };
      row.forecast = day.eur;
      row.band = [day.low_eur, day.high_eur];
      rows.set(day.date, row);
    }
  }
  for (const anomaly of anomalies) {
    const row = rows.get(anomaly.date);
    if (row) row.anomalies = [...(row.anomalies ?? []), anomaly];
  }
  return [...rows.values()].sort((a, b) => a.date.localeCompare(b.date));
}

export function CostTimeline({
  data,
  focus,
  forecast = null,
  anomalies = [],
  granularity = "daily",
}: {
  data: CostSeriesPoint[];
  /** Quand défini, n'affiche que cette ressource (piloté par le donut). */
  focus?: FinopsResource | null;
  /** Prévision quotidienne jusqu'à la fin du mois (vue quotidienne). */
  forecast?: CostForecast | null;
  anomalies?: CostAnomaly[];
  granularity?: string;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  // Légende interactive : ressources masquées au clic.
  const [hidden, setHidden] = useState<Set<FinopsResource>>(new Set());

  const toggle = (r: FinopsResource) => {
    if (focus) return; // focus piloté par le donut : légende gelée
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(r)) next.delete(r);
      else next.add(r);
      return next;
    });
  };

  const RESOURCES = visibleResources(
    data.some((p) => (p.ai ?? 0) > 0),
    data.some((p) => (p.platform ?? 0) > 0),
  );

  const isHidden = (r: FinopsResource) => (focus ? r !== focus : hidden.has(r));

  const bucketSize: Granularity =
    granularity === "weekly" || granularity === "monthly" ? granularity : "daily";
  const partialBucket = useMemo(
    () => findPartialBucket(data, forecast, bucketSize, new Date()),
    [data, forecast, bucketSize],
  );
  const rows = buildRows(
    data,
    forecast,
    anomalies,
    partialBucket,
    RESOURCES,
    isHidden,
  );
  const stackedRows: StackedRow[] = rows.map((row) => {
    if (row.total === undefined) return row;
    return { ...row, stack: stackOf(row, RESOURCES, isHidden) };
  });
  const tooltipResources = RESOURCES.filter((r) => !isHidden(r)).map((r) => ({
    key: r,
    name: t(`resources.${r}`),
    color: RESOURCE_META[r].color,
  }));
  const axisMax = Math.max(
    0,
    ...rows.flatMap((r) => [
      r.total ?? 0,
      r.forecast ?? 0,
      r.band?.[1] ?? 0,
      r.projection ?? 0,
    ]),
  );
  const markers = rows.filter((r) => r.anomalies?.length);
  const hasForecast = !!forecast && forecast.daily.length > 0;

  return (
    <div className="space-y-3">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={stackedRows}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          >
            <defs>
              {RESOURCES.map((r) => (
                <linearGradient
                  key={r}
                  id={`grad-${r}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="5%"
                    stopColor={RESOURCE_META[r].color}
                    stopOpacity={0.5}
                  />
                  <stop
                    offset="95%"
                    stopColor={RESOURCE_META[r].color}
                    stopOpacity={0.05}
                  />
                </linearGradient>
              ))}
              {RESOURCES.map((r) => (
                <pattern
                  key={r}
                  id={`hatch-${r}`}
                  width="6"
                  height="6"
                  patternUnits="userSpaceOnUse"
                  patternTransform="rotate(45)"
                >
                  <rect width="6" height="6" fill={RESOURCE_META[r].color} fillOpacity={0.08} />
                  <line x1="0" y1="0" x2="0" y2="6" stroke={RESOURCE_META[r].color} strokeWidth={2} strokeOpacity={0.55} />
                </pattern>
              ))}
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              className="stroke-muted"
              vertical={false}
            />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              minTickGap={24}
            />
            <YAxis
              tick={{ fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={68}
              tickFormatter={axisTickFormatter(axisMax, " €", locale)}
            />
            <Tooltip
              content={
                <CostTooltip resources={tooltipResources} />
              }
            />
            {RESOURCES.map((r) => (
              <Area
                key={r}
                type="linear"
                // A precomputed [low, high] range, not stackId: Recharts stacks a
                // missing value as 0, which drew a cliff after the last real day.
                dataKey={(row: StackedRow) => row.stack?.[r]}
                name={t(`resources.${r}`)}
                stroke={RESOURCE_META[r].color}
                fill={`url(#grad-${r})`}
                strokeWidth={2}
                hide={isHidden(r)}
                isAnimationActive
                animationDuration={300}
              />
            ))}
            {partialBucket &&
              RESOURCES.map((r) => (
                <Area
                  key={`partial-${r}`}
                  type="linear"
                  dataKey={(row: StackedRow) => row.partialStack?.[r]}
                  name={t(`resources.${r}`)}
                  stroke={RESOURCE_META[r].color}
                  strokeDasharray="3 3"
                  fill={`url(#hatch-${r})`}
                  strokeWidth={1.5}
                  hide={isHidden(r)}
                  activeDot={false}
                  isAnimationActive={false}
                />
              ))}
            {hasForecast && (
              <Area
                type="linear"
                dataKey="band"
                name={t("timeline.forecastRange")}
                stroke="none"
                fill={FORECAST_COLOR}
                fillOpacity={0.15}
                activeDot={false}
                isAnimationActive={false}
              />
            )}
            {hasForecast && (
              <Line
                type="linear"
                dataKey="forecast"
                name={t("timeline.forecast")}
                stroke={FORECAST_COLOR}
                strokeWidth={2}
                strokeDasharray="5 4"
                dot={false}
                isAnimationActive={false}
              />
            )}
            {partialBucket && (
              <Line
                type="linear"
                dataKey="projection"
                name={t("timeline.projectedEnd", { unit: bucketSize })}
                stroke={FORECAST_COLOR}
                strokeWidth={2}
                strokeDasharray="5 4"
                dot={false}
                activeDot={false}
                isAnimationActive={false}
              />
            )}
            {partialBucket && (
              <ReferenceDot
                x={partialBucket.date}
                y={partialBucket.projected}
                r={5}
                fill="var(--background)"
                stroke={FORECAST_COLOR}
                strokeWidth={2}
              />
            )}
            {markers.map((r) => (
              <ReferenceDot
                key={r.date}
                x={r.date}
                y={RESOURCES.filter((res) => !isHidden(res)).reduce(
                  (s, res) => s + (r[res] ?? 0),
                  0,
                )}
                r={5}
                fill={ANOMALY_COLOR}
                stroke="white"
                strokeWidth={1.5}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Légende interactive */}
      <div className="flex flex-wrap justify-center gap-3">
        {RESOURCES.map((r) => {
          const off = isHidden(r);
          return (
            <button
              key={r}
              title={r === "platform" ? t("resourceHints.platform") : undefined}
              onClick={() => toggle(r)}
              className="flex items-center gap-1.5 text-xs transition-opacity"
              style={{ opacity: off ? 0.4 : 1 }}
            >
              <span
                className="h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: RESOURCE_META[r].color }}
              />
              <span className={off ? "line-through" : ""}>
                {t(`resources.${r}`)}
              </span>
            </button>
          );
        })}
        {hasForecast && (
          <span className="flex items-center gap-1.5 text-xs">
            <span
              className="w-4 border-t-2 border-dashed"
              style={{ borderColor: FORECAST_COLOR }}
            />
            {t("timeline.forecastLegend", {
              confidence: Math.round(forecast!.confidence * 100),
            })}
          </span>
        )}
        {partialBucket && (
          <>
            <span className="flex items-center gap-1.5 text-xs">
              <span
                className="h-2.5 w-2.5 rounded-sm border"
                style={{
                  borderColor: FORECAST_COLOR,
                  backgroundImage: `repeating-linear-gradient(45deg, ${FORECAST_COLOR} 0 1px, transparent 1px 4px)`,
                }}
              />
              {t("timeline.soFar", {
                unit: bucketSize,
                pct: Math.round(partialBucket.elapsed * 100),
              })}
            </span>
            <span className="flex items-center gap-1.5 text-xs">
              <span
                className="w-4 border-t-2 border-dashed"
                style={{ borderColor: FORECAST_COLOR }}
              />
              {t("timeline.projectedEnd", { unit: bucketSize })}
            </span>
          </>
        )}
        {markers.length > 0 && (
          <span className="flex items-center gap-1.5 text-xs">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: ANOMALY_COLOR }}
            />
            {t("timeline.anomaly")}
          </span>
        )}
      </div>
    </div>
  );
}
