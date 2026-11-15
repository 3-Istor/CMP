"use client";

import type { DeployMarker, ObsPoint } from "@/types/observability";
import { useTranslations } from "next-intl";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useObsFormat } from "./shared";

const TOOLTIP_STYLE = {
  borderRadius: 8,
  fontSize: 12,
  background: "var(--popover)",
  border: "1px solid var(--border)",
};
const FAILED = new Set(["aborted_auto", "aborted_manual", "failed"]);

/**
 * One signal over the range, with a vertical marker per prod deploy. The
 * chart is an image to assistive tech; its label carries the last value.
 */
export function SignalChart({
  name,
  points,
  markers,
  format,
  color = "var(--chart-1)",
}: {
  name: string;
  points: ObsPoint[];
  markers: DeployMarker[];
  format: (value: number) => string;
  color?: string;
}) {
  const t = useTranslations("obs.signals");
  const { clock, time } = useObsFormat();
  const data = points.map((p) => ({ t: new Date(p.time).getTime(), v: p.value }));
  const last = points.at(-1);
  const first = data[0]?.t ?? 0;
  const end = data.at(-1)?.t ?? 0;
  const visible = markers
    .map((m) => ({ ...m, at: new Date(m.time).getTime() }))
    .filter((m) => m.at >= first && m.at <= end);
  const label = t("chartLabel", {
    name,
    value: last ? format(last.value) : "-",
  });
  return (
    <figure className="space-y-1">
      <figcaption className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">{name}</span>
        <span className="tabular-nums text-muted-foreground">
          {last ? t("now", { value: format(last.value) }) : "-"}
        </span>
      </figcaption>
      <div className="h-36" role="img" aria-label={label}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} strokeOpacity={0.15} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(v: number) => clock(new Date(v)).slice(0, 5)}
              tick={{ fontSize: 11 }}
              tickLine={false}
              minTickGap={40}
            />
            <YAxis
              width={68}
              tick={{ fontSize: 11 }}
              tickLine={false}
              tickFormatter={(v: number) => format(v)}
            />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelFormatter={(v) => time(new Date(Number(v)))}
              formatter={(value) => [format(Number(value)), name]}
            />
            {visible.map((m) => (
              <ReferenceLine
                key={`${m.at}-${m.component ?? ""}`}
                x={m.at}
                stroke={FAILED.has(m.outcome) ? "var(--destructive)" : "var(--chart-3)"}
                strokeDasharray="4 3"
                label={{
                  value: m.version ?? "",
                  position: "insideTopLeft",
                  fontSize: 10,
                  fill: "var(--muted-foreground)",
                }}
              />
            ))}
            <Line
              type="monotone"
              dataKey="v"
              stroke={color}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {visible.length > 0 && (
        <ul className="sr-only">
          {visible.map((m) => (
            <li key={`${m.at}-${m.component ?? ""}`}>
              {t("deployMarker", { version: m.version ?? "", outcome: m.outcome })}{" "}
              {time(m.time)}
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}
