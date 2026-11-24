"use client";

import { getLogVolume } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { LogVolume } from "@/types";
import { BarChart3, ChevronDown } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { NativeSelect } from "./NativeSelect";
import type { Translate } from "./labels";

const COLOURS = [
  "#2563eb",
  "#059669",
  "#7c3aed",
  "#ea580c",
  "#db2777",
  "#0d9488",
  "#ca8a04",
];
const PERIODS = [
  { hours: 1, labelKey: "1h" },
  { hours: 6, labelKey: "6h" },
  { hours: 24, labelKey: "24h" },
  { hours: 168, labelKey: "7d" },
];

function size(t: Translate, bytes: number): string {
  if (bytes >= 1e9)
    return t("volume.size.gb", { value: (bytes / 1e9).toFixed(2) });
  if (bytes >= 1e6)
    return t("volume.size.mb", { value: (bytes / 1e6).toFixed(1) });
  if (bytes >= 1e3)
    return t("volume.size.kb", { value: (bytes / 1e3).toFixed(0) });
  return t("volume.size.b", { value: bytes });
}

/** How much each app logs: a sudden climb is often the first sign of trouble. */
export function VolumeCard({
  project,
  namespace,
}: {
  project: string;
  namespace?: string;
}) {
  const t = useTranslations("activity");
  const locale = useLocale();
  const [open, setOpen] = useState(true);
  const [hours, setHours] = useState(24);
  const [volume, setVolume] = useState<LogVolume | null>(null);

  useEffect(() => {
    if (!open) return;
    getLogVolume(project, hours, namespace).then(setVolume, () =>
      setVolume(null),
    );
  }, [project, hours, namespace, open]);

  const name = (ns: string) => ns.replace(`${project}-`, "");
  const data = useMemo(() => {
    if (!volume) return [];
    const perMinute = volume.step_seconds / 60;
    const rows = new Map<string, Record<string, number | string>>();
    for (const s of volume.series) {
      for (const p of s.points) {
        const row = rows.get(p.time) ?? {
          time: new Date(p.time).toLocaleString(locale, {
            day: hours > 24 ? "numeric" : undefined,
            month: hours > 24 ? "short" : undefined,
            hour: "2-digit",
            minute: "2-digit",
          }),
        };
        row[name(s.namespace)] = Math.round((p.lines / perMinute) * 10) / 10;
        rows.set(p.time, row);
      }
    }
    return [...rows.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, r]) => r);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [volume, hours, locale]);

  const totalBytes = volume?.series.reduce((a, s) => a + s.total_bytes, 0) ?? 0;

  return (
    <div className="rounded-xl border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <BarChart3 className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{t("volume.title")}</span>
        {volume && (
          <span className="text-muted-foreground">
            {t("volume.total", { size: size(t, totalBytes) })}
          </span>
        )}
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="space-y-3 border-t p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {t("volume.description")}
            </p>
            <div className="w-36">
              <NativeSelect
                aria-label={t("volume.aria")}
                value={String(hours)}
                onChange={(v) => setHours(Number(v))}
              >
                {PERIODS.map((p) => (
                  <option key={p.hours} value={p.hours}>
                    {t(`periods.${p.labelKey}`)}
                  </option>
                ))}
              </NativeSelect>
            </div>
          </div>
          {!volume ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t("common.loading")}
            </p>
          ) : volume.series.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t("volume.empty")}
            </p>
          ) : (
            <>
              <div className="h-44">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart
                    data={data}
                    margin={{ top: 4, right: 4, left: -16, bottom: 0 }}
                  >
                    <CartesianGrid vertical={false} strokeOpacity={0.15} />
                    <XAxis
                      dataKey="time"
                      tick={{ fontSize: 11 }}
                      tickLine={false}
                      minTickGap={32}
                    />
                    <YAxis tick={{ fontSize: 11 }} tickLine={false} />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 8,
                        fontSize: 12,
                        background: "var(--popover)",
                        border: "1px solid var(--border)",
                      }}
                      formatter={(v) => [t("volume.tooltip", { value: String(v) })]}
                    />
                    {volume.series.length > 1 && (
                      <Legend
                        iconType="circle"
                        wrapperStyle={{ fontSize: 12 }}
                      />
                    )}
                    {volume.series.map((s, i) => (
                      <Area
                        key={s.namespace}
                        type="monotone"
                        dataKey={name(s.namespace)}
                        stackId="v"
                        stroke={COLOURS[i % COLOURS.length]}
                        fill={COLOURS[i % COLOURS.length]}
                        fillOpacity={0.25}
                      />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1 font-medium">{t("volume.table.app")}</th>
                    <th className="py-1 text-right font-medium">{t("volume.table.size")}</th>
                    <th className="py-1 text-right font-medium">{t("volume.table.lines")}</th>
                    <th className="py-1 text-right font-medium">{t("volume.table.peak")}</th>
                  </tr>
                </thead>
                <tbody>
                  {volume.series.map((s, i) => (
                    <tr key={s.namespace} className="border-t">
                      <td className="py-1">
                        <span
                          className="mr-2 inline-block h-2 w-2 rounded-full"
                          style={{ background: COLOURS[i % COLOURS.length] }}
                        />
                        {name(s.namespace)}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {size(t, s.total_bytes)}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {s.total_lines.toLocaleString(locale)}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {Math.round(s.peak_per_minute).toLocaleString(locale)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}
