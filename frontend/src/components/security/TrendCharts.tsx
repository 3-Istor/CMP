"use client";

import type { SecurityTrendPoint } from "@/types";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { TIER_META } from "./labels";

const TOOLTIP_STYLE = {
  borderRadius: 8,
  fontSize: 12,
  background: "var(--popover)",
  border: "1px solid var(--border)",
};

/** Monday of the week of an ISO day, as "12 oct.". */
function weekLabel(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

function weekly(points: SecurityTrendPoint[]) {
  const weeks = new Map<string, number>();
  for (const p of points) {
    const key = weekLabel(p.day);
    weeks.set(key, (weeks.get(key) ?? 0) + p.new_major);
  }
  return [...weeks.entries()].slice(-12).map(([week, count]) => ({ week, count }));
}

export function NewMajorChart({ points }: { points: SecurityTrendPoint[] }) {
  const data = weekly(points);
  if (data.length === 0) {
    return <p className="text-sm text-muted-foreground">Pas encore d&apos;historique.</p>;
  }
  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
          <CartesianGrid vertical={false} strokeOpacity={0.15} />
          <XAxis dataKey="week" tick={{ fontSize: 11 }} tickLine={false} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value) => [String(value), "Nouvelles failles majeures"]}
            labelFormatter={(label) => `Semaine du ${label}`}
          />
          <Bar dataKey="count" fill={TIER_META.important.color} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScoreTrendChart({ points }: { points: SecurityTrendPoint[] }) {
  if (points.length < 2) {
    return <p className="text-sm text-muted-foreground">Pas encore d&apos;historique.</p>;
  }
  const data = points.map((p) => ({
    day: new Date(`${p.day}T00:00:00Z`).toLocaleDateString("fr-FR", {
      day: "numeric",
      month: "short",
    }),
    score: p.score,
    grade: p.grade,
  }));
  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
          <CartesianGrid vertical={false} strokeOpacity={0.15} />
          <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} minTickGap={24} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} tickLine={false} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, _name, item) => [
              `${value}/100 · ${item.payload.grade}`,
              "Score",
            ]}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke="var(--primary)"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
