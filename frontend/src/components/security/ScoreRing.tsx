"use client";

import type { SecurityCategory, SecuritySegment } from "@/types";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { CATEGORY_META, EMPTY_COLOR, OK_COLOR, TIER_META } from "./labels";

const TIER_ORDER = ["core", "important", "recommended"] as const;

function segmentColor(segment: SecuritySegment): string {
  if (segment.category === "journal") return EMPTY_COLOR;
  return segment.worst ? TIER_META[segment.worst].color : OK_COLOR;
}

function countLine(segment: SecuritySegment): string {
  const parts = TIER_ORDER.filter((t) => segment.counts[t]).map(
    (t) => `${segment.counts[t]} ${TIER_META[t].label.toLowerCase()}`,
  );
  return parts.length ? parts.join(" · ") : "Rien à faire";
}

/**
 * One equal segment per category, coloured by the worst open tier in it.
 * Hover shows the counts and points lost; a click jumps to the category.
 */
export function ScoreRing({
  score,
  grade,
  actions,
  segments,
  onSelect,
}: {
  score: number;
  grade: string;
  actions: number;
  segments: SecuritySegment[];
  onSelect: (category: SecurityCategory) => void;
}) {
  const data = segments.map((s) => ({ ...s, value: 1 }));

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
      <div className="relative h-44 w-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="label"
              innerRadius={56}
              outerRadius={80}
              paddingAngle={3}
              startAngle={90}
              endAngle={-270}
              isAnimationActive={false}
              onClick={(_, index) => onSelect(data[index].category)}
            >
              {data.map((s) => (
                <Cell
                  key={s.category}
                  fill={segmentColor(s)}
                  cursor="pointer"
                  stroke="transparent"
                />
              ))}
            </Pie>
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const segment = payload[0].payload as SecuritySegment;
                return (
                  <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-sm">
                    <p className="font-medium">
                      {CATEGORY_META[segment.category].question}
                    </p>
                    <p className="text-muted-foreground">
                      {segment.category === "journal"
                        ? "Arrive avec la phase 2"
                        : countLine(segment)}
                    </p>
                    {segment.points_lost > 0 && (
                      <p className="text-muted-foreground">
                        −{segment.points_lost} points
                      </p>
                    )}
                  </div>
                );
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-3xl font-semibold tabular-nums">{score}</span>
          <span className="text-xs text-muted-foreground">
            /100 · note {grade}
          </span>
        </div>
      </div>

      <div className="w-full space-y-1">
        <p className="mb-2 text-sm font-semibold">
          {actions === 0
            ? "Rien à faire"
            : `${actions} action${actions > 1 ? "s" : ""} à faire`}
        </p>
        {segments.map((s) => (
          <button
            key={s.category}
            type="button"
            onClick={() => onSelect(s.category)}
            className="flex w-full items-center gap-2 rounded-md px-1.5 py-0.5 text-left text-sm transition-colors hover:bg-accent"
          >
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: segmentColor(s) }}
            />
            <span className="flex-1">{CATEGORY_META[s.category].label}</span>
            <span className="text-xs text-muted-foreground">
              {s.category === "journal"
                ? "phase 2"
                : Object.values(s.counts).reduce((a, b) => a + (b ?? 0), 0) ||
                  "ok"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
