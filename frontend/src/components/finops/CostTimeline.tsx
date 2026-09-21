"use client";

import type { CostSeriesPoint, FinopsResource } from "@/types";
import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { RESOURCE_META, RESOURCES, formatEUR } from "./format";

function CostTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const total = payload.reduce((s, p) => s + (p.value ?? 0), 0);
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
          <span className="ml-auto tabular-nums">{formatEUR(p.value)}</span>
        </p>
      ))}
      <p className="mt-1 flex justify-between border-t pt-1 font-medium">
        <span>Total</span>
        <span className="tabular-nums">{formatEUR(total)}</span>
      </p>
    </div>
  );
}

export function CostTimeline({
  data,
  focus,
}: {
  data: CostSeriesPoint[];
  /** Quand défini, n'affiche que cette ressource (piloté par le donut). */
  focus?: FinopsResource | null;
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

  const isHidden = (r: FinopsResource) =>
    focus ? r !== focus : hidden.has(r);

  return (
    <div className="space-y-3">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data}
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
          </AreaChart>
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
      </div>
    </div>
  );
}
