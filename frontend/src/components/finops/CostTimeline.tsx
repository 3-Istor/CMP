"use client";

import type {
  CostAnomaly,
  CostForecast,
  CostSeriesPoint,
  FinopsResource,
} from "@/types";
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
  RESOURCES as ALL_RESOURCES,
  RESOURCE_META,
  formatEUR,
  visibleResources,
} from "./format";

const FORECAST_COLOR = "#64748B";
const ANOMALY_COLOR = "#EF4444";

type TimelineRow = Partial<CostSeriesPoint> & {
  date: string;
  forecast?: number;
  band?: [number, number];
  anomalies?: CostAnomaly[];
};

function CostTooltip({
  active,
  payload,
  label,
}: {
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
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  const costs = payload.filter((p) =>
    ALL_RESOURCES.includes(p.dataKey as FinopsResource),
  );
  const total = costs.reduce((s, p) => s + (p.value ?? 0), 0);
  return (
    <div className="max-w-xs rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-medium">{label}</p>
      {costs.map((p) => (
        <p key={p.name} className="flex items-center gap-1.5">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: p.color }}
          />
          {p.name}
          <span className="ml-auto tabular-nums">{formatEUR(p.value)}</span>
        </p>
      ))}
      {costs.length > 0 && (
        <p className="mt-1 flex justify-between border-t pt-1 font-medium">
          <span>Total</span>
          <span className="tabular-nums">{formatEUR(total)}</span>
        </p>
      )}
      {row?.band && row.total === undefined && (
        <p className="flex justify-between gap-3">
          <span>Prévision</span>
          <span className="tabular-nums">
            {formatEUR(row.forecast ?? 0)} ({formatEUR(row.band[0])} –{" "}
            {formatEUR(row.band[1])})
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
            Anomalie · {a.app_name} : {formatEUR(a.cost_eur)} (médiane{" "}
            {formatEUR(a.baseline_eur)})
          </p>
          <p className="text-muted-foreground">Cause probable : {a.cause}</p>
        </div>
      ))}
    </div>
  );
}

function buildRows(
  data: CostSeriesPoint[],
  forecast: CostForecast | null,
  anomalies: CostAnomaly[],
): TimelineRow[] {
  const rows = new Map<string, TimelineRow>(
    data.map((p) => [p.date, { ...p }]),
  );
  if (forecast && forecast.daily.length > 0) {
    // Start the dashed line on the last real point so the two connect.
    const lastReal = data.findLast(
      (p) => p.date < forecast.daily[0].date,
    );
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
}: {
  data: CostSeriesPoint[];
  /** Quand défini, n'affiche que cette ressource (piloté par le donut). */
  focus?: FinopsResource | null;
  /** Prévision quotidienne jusqu'à la fin du mois (vue quotidienne). */
  forecast?: CostForecast | null;
  anomalies?: CostAnomaly[];
}) {
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

  const RESOURCES = visibleResources(data.some((p) => (p.ai ?? 0) > 0));

  const isHidden = (r: FinopsResource) =>
    focus ? r !== focus : hidden.has(r);

  const rows = useMemo(
    () => buildRows(data, forecast, anomalies),
    [data, forecast, anomalies],
  );
  const markers = rows.filter((r) => r.anomalies?.length);
  const hasForecast = !!forecast && forecast.daily.length > 0;

  return (
    <div className="space-y-3">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={rows}
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
              width={48}
              tickFormatter={(v: number) => `${v.toFixed(0)}€`}
            />
            <Tooltip content={<CostTooltip />} />
            {RESOURCES.map((r) => (
              <Area
                key={r}
                type="monotone"
                dataKey={r}
                name={RESOURCE_META[r].label}
                stackId="cost"
                stroke={RESOURCE_META[r].color}
                fill={`url(#grad-${r})`}
                strokeWidth={2}
                hide={isHidden(r)}
                isAnimationActive
                animationDuration={300}
              />
            ))}
            {hasForecast && (
              <Area
                type="monotone"
                dataKey="band"
                name="Intervalle de prévision"
                stroke="none"
                fill={FORECAST_COLOR}
                fillOpacity={0.15}
                activeDot={false}
                isAnimationActive={false}
              />
            )}
            {hasForecast && (
              <Line
                type="monotone"
                dataKey="forecast"
                name="Prévision"
                stroke={FORECAST_COLOR}
                strokeWidth={2}
                strokeDasharray="5 4"
                dot={false}
                isAnimationActive={false}
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
              onClick={() => toggle(r)}
              className="flex items-center gap-1.5 text-xs transition-opacity"
              style={{ opacity: off ? 0.4 : 1 }}
            >
              <span
                className="h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: RESOURCE_META[r].color }}
              />
              <span className={off ? "line-through" : ""}>
                {RESOURCE_META[r].label}
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
            Prévision ({Math.round(forecast!.confidence * 100)} %)
          </span>
        )}
        {markers.length > 0 && (
          <span className="flex items-center gap-1.5 text-xs">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: ANOMALY_COLOR }}
            />
            Anomalie
          </span>
        )}
      </div>
    </div>
  );
}
