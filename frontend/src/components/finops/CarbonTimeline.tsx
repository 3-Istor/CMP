"use client";

import type { FinopsCarbon } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import { axisTickFormatter, intlLocale } from "./format";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export const CARBON_COLORS = { energy: "#3B82F6", intensity: "#10B981" };

const SERIES = [
  { key: "energy", color: CARBON_COLORS.energy },
  { key: "intensity", color: CARBON_COLORS.intensity },
] as const;

function CarbonTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number | null; color: string }>;
  label?: string;
}) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-medium">{label}</p>
      {payload.map((p) => (
        <p key={p.name} className="flex items-center gap-1.5">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: p.color }}
          />
          {p.name}
          <span className="ml-auto pl-4 tabular-nums">
            {p.value === null || p.value === undefined
              ? t("common.na")
              : p.value.toLocaleString(locale, { maximumFractionDigits: 2 })}
          </span>
        </p>
      ))}
    </div>
  );
}

/** Énergie en barres (axe gauche), intensité du réseau en ligne (axe droit). */
export function CarbonTimeline({ data }: { data: FinopsCarbon["timeline"] }) {
  const t = useTranslations("finops");
  const locale = intlLocale(useLocale());
  const energyMax = Math.max(0, ...data.map((d) => d.energy_kwh ?? 0));
  const series = SERIES.map((s) => ({
    ...s,
    name: t(`carbon.series.${s.key}`),
  }));
  return (
    <div className="space-y-3">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          >
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
              yAxisId="kwh"
              tick={{ fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={68}
              tickFormatter={axisTickFormatter(energyMax, "", locale)}
            />
            <YAxis
              yAxisId="intensity"
              orientation="right"
              tick={{ fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={40}
            />
            <Tooltip content={<CarbonTooltip />} />
            <Bar
              yAxisId="kwh"
              dataKey="energy_kwh"
              name={series[0].name}
              fill={series[0].color}
              radius={[3, 3, 0, 0]}
              isAnimationActive
              animationDuration={300}
            />
            <Line
              yAxisId="intensity"
              type="monotone"
              dataKey="g_per_kwh"
              name={series[1].name}
              stroke={series[1].color}
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive
              animationDuration={300}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap justify-center gap-3">
        {series.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5 text-xs">
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: s.color }}
            />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
