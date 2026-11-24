"use client";

import { cn } from "@/lib/utils";
import { useFormatter, useTranslations } from "next-intl";
import { useId } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EmptyState, type EmptyStateProps } from "./EmptyState";

export interface TimeSeries {
  /** The field of each data point holding this series' value. */
  key: string;
  label: string;
  /** Any CSS colour, e.g. "var(--chart-1)". */
  color: string;
  type: "area" | "line" | "bar";
  /** Series sharing a stack id are stacked. */
  stack?: string;
  dashed?: boolean;
}

export interface TimePoint {
  /** Epoch milliseconds. */
  t: number;
  [series: string]: number | null;
}

export interface DeployMarker {
  t: number;
  label: string;
  failed?: boolean;
}

export interface TimeChartProps {
  series: TimeSeries[];
  /** Oldest first, evenly spaced buckets. */
  data: TimePoint[];
  deployMarkers?: DeployMarker[];
  /** The last bucket is still filling: it is hatched so a dip does not read as a drop. */
  partialLast?: boolean;
  /** Per series key, the value the partial last bucket is projected to reach. */
  projected?: Record<string, number>;
  /** Pixels. */
  height?: number;
  loading?: boolean;
  /** Shown instead of the chart when there is no point to draw. */
  empty?: EmptyStateProps;
  yFormat?: (value: number) => string;
  /** The accessible name of the chart. */
  label?: string;
  /** Shows the legend under the chart; on by default with two series or more. */
  legend?: boolean;
  className?: string;
}

const TOOLTIP_STYLE = {
  borderRadius: 8,
  fontSize: 12,
  background: "var(--popover)",
  border: "1px solid var(--border)",
};

export function TimeChart({
  series,
  data,
  deployMarkers = [],
  partialLast = false,
  projected,
  height = 160,
  loading = false,
  empty,
  yFormat,
  label,
  legend,
  className,
}: TimeChartProps) {
  const format = useFormatter();
  const t = useTranslations("data.chart");
  const hatch = `hatch-${useId().replace(/:/g, "")}`;
  const yFmt = yFormat ?? ((v: number) => format.number(v, { notation: "compact" }));
  const clock = (ms: number) =>
    format.dateTime(new Date(ms), { hour: "2-digit", minute: "2-digit" });
  const stamp = (ms: number) =>
    format.dateTime(new Date(ms), {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });

  if (loading) {
    return (
      <div
        className={cn("w-full animate-pulse rounded-lg bg-muted", className)}
        style={{ height }}
        aria-busy
      />
    );
  }
  if (data.length === 0) {
    return (
      <div
        className={cn("flex w-full items-center justify-center", className)}
        style={{ height }}
      >
        <EmptyState compact title={t("noData")} {...empty} />
      </div>
    );
  }

  const first = data[0].t;
  const last = data[data.length - 1].t;
  const step = data.length > 1 ? last - data[data.length - 2].t : 0;
  const markers = deployMarkers.filter((m) => m.t >= first && m.t <= last);
  const showLegend = legend ?? series.length > 1;

  return (
    <figure className={cn("w-full", className)}>
      <div style={{ height }} role="img" aria-label={label}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <defs>
              <pattern
                id={hatch}
                width="6"
                height="6"
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <line x1="0" y1="0" x2="0" y2="6" stroke="var(--muted-foreground)" strokeOpacity={0.35} strokeWidth={2} />
              </pattern>
            </defs>
            <CartesianGrid vertical={false} strokeOpacity={0.15} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", step && partialLast ? `dataMax + ${step / 2}` : "dataMax"]}
              tickFormatter={clock}
              tick={{ fontSize: 11 }}
              tickLine={false}
              minTickGap={40}
            />
            <YAxis
              width={56}
              tick={{ fontSize: 11 }}
              tickLine={false}
              tickFormatter={yFmt}
            />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelFormatter={(v) => stamp(Number(v))}
              formatter={(value, name) => [
                value == null ? "-" : yFmt(Number(value)),
                series.find((s) => s.key === name)?.label ?? String(name),
              ]}
            />
            {showLegend && (
              <Legend
                iconSize={8}
                wrapperStyle={{ fontSize: 11 }}
                formatter={(key) => series.find((s) => s.key === key)?.label ?? key}
              />
            )}
            {partialLast && step > 0 && (
              <ReferenceArea
                x1={last - step / 2}
                x2={last + step / 2}
                fill={`url(#${hatch})`}
                strokeOpacity={0}
                label={{
                  value: t("partial"),
                  position: "insideTop",
                  fontSize: 10,
                  fill: "var(--muted-foreground)",
                }}
              />
            )}
            {series.map((s) => {
              const dash = s.dashed ? "4 3" : undefined;
              if (s.type === "bar") {
                return (
                  <Bar
                    key={s.key}
                    dataKey={s.key}
                    stackId={s.stack}
                    fill={s.color}
                    isAnimationActive={false}
                  />
                );
              }
              if (s.type === "area") {
                return (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    stackId={s.stack}
                    stroke={s.color}
                    fill={s.color}
                    fillOpacity={0.2}
                    strokeDasharray={dash}
                    isAnimationActive={false}
                    connectNulls={false}
                  />
                );
              }
              return (
                <Line
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  stroke={s.color}
                  strokeWidth={2}
                  strokeDasharray={dash}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
              );
            })}
            {partialLast &&
              projected &&
              series
                .filter((s) => projected[s.key] !== undefined)
                .map((s) => (
                  <ReferenceDot
                    key={`projected-${s.key}`}
                    x={last}
                    y={projected[s.key]}
                    r={4}
                    fill="var(--background)"
                    stroke={s.color}
                    strokeDasharray="2 2"
                    label={{
                      value: t("projected", { value: yFmt(projected[s.key]) }),
                      position: "left",
                      fontSize: 10,
                      fill: "var(--muted-foreground)",
                    }}
                  />
                ))}
            {markers.map((m) => (
              <ReferenceLine
                key={`${m.t}-${m.label}`}
                x={m.t}
                stroke={m.failed ? "var(--destructive)" : "var(--chart-3)"}
                strokeDasharray="4 3"
                label={{
                  value: m.label,
                  position: "insideTopLeft",
                  fontSize: 10,
                  fill: "var(--muted-foreground)",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {markers.length > 0 && (
        <ul className="sr-only">
          {markers.map((m) => (
            <li key={`${m.t}-${m.label}`}>
              {t("deployMarker", { label: m.label, time: stamp(m.t) })}
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}
