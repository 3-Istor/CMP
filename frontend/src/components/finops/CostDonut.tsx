"use client";

import type { CostBreakdown, FinopsResource } from "@/types";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { RESOURCE_META, RESOURCES, formatEUR } from "./format";

/**
 * Donut de répartition des coûts par ressource. Un clic sur un segment appelle
 * ``onSelect`` (filtre le reste de la page) ; recliquer la même ressource
 * réinitialise le filtre.
 */
export function CostDonut({
  breakdown,
  selected,
  onSelect,
}: {
  breakdown: CostBreakdown;
  selected?: FinopsResource | null;
  onSelect?: (r: FinopsResource | null) => void;
}) {
  const data = RESOURCES.map((r) => ({
    resource: r,
    name: RESOURCE_META[r].label,
    value: breakdown[r] ?? 0,
    color: RESOURCE_META[r].color,
  }));
  const total = data.reduce((s, d) => s + d.value, 0);

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius={60}
              outerRadius={90}
              paddingAngle={2}
              isAnimationActive
              animationDuration={300}
              onClick={(_, index) => {
                const r = data[index].resource;
                onSelect?.(selected === r ? null : r);
              }}
            >
              {data.map((d) => (
                <Cell
                  key={d.resource}
                  fill={d.color}
                  cursor={onSelect ? "pointer" : "default"}
                  opacity={selected && selected !== d.resource ? 0.35 : 1}
                  stroke={selected === d.resource ? d.color : "transparent"}
                  strokeWidth={2}
                />
              ))}
            </Pie>
            <Tooltip
              formatter={(value: number, name: string) => [
                formatEUR(value),
                name,
              ]}
              contentStyle={{
                borderRadius: 8,
                fontSize: 12,
                background: "var(--popover)",
                border: "1px solid var(--border)",
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xs text-muted-foreground">Total</span>
          <span className="text-lg font-semibold tabular-nums">
            {formatEUR(total)}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap justify-center gap-2">
        {data.map((d) => (
          <button
            key={d.resource}
            onClick={() => onSelect?.(selected === d.resource ? null : d.resource)}
            className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs transition-colors hover:bg-accent"
            style={{ opacity: selected && selected !== d.resource ? 0.4 : 1 }}
          >
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: d.color }}
            />
            {d.name}
            <span className="tabular-nums text-muted-foreground">
              {formatEUR(d.value)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
