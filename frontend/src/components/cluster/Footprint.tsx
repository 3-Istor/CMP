"use client";

import { type AppFootprint, getProjectFootprint } from "@/lib/api";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const MIB = 2 ** 20;
const GIB = 2 ** 30;

export const memoryLabel = (bytes: number) => (bytes >= GIB ? `${(bytes / GIB).toFixed(1)} Gi` : `${Math.round(bytes / MIB)} Mi`);
export const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

/** "0.10 CPU · 128 Mi": what an app requests, measured while its cluster was up. */
export function FootprintLine({ row }: { row: Pick<AppFootprint, "cpu" | "memory_bytes" | "measured_at"> }) {
  const t = useTranslations("cluster");
  return (
    <span className="text-[11px] tabular-nums text-muted-foreground" title={t("footprintMeasured", { time: clock(row.measured_at) })}>
      {row.cpu.toFixed(2)} CPU · {memoryLabel(row.memory_bytes)}
    </span>
  );
}

/** Project total on the card row; "last seen" (oldest measure) when its cluster is not up. */
export function FootprintTotal({ rows, up }: { rows: AppFootprint[]; up: boolean }) {
  const t = useTranslations("cluster");
  if (rows.length === 0) return null;
  const oldest = rows.reduce((a, r) => (r.measured_at < a ? r.measured_at : a), rows[0].measured_at);
  return (
    <span className="text-xs tabular-nums text-muted-foreground">
      {t("footprintTotal")} {rows.reduce((s, r) => s + r.cpu, 0).toFixed(2)} CPU · {memoryLabel(rows.reduce((s, r) => s + r.memory_bytes, 0))}
      {!up && ` · ${t("footprintLastSeen", { time: clock(oldest) })}`}
    </span>
  );
}

/** The project's footprint on one cloud; empty until read or when none is stored. */
export function useProjectFootprint(project: string, cloud: string | null) {
  const [rows, setRows] = useState<AppFootprint[]>([]);
  useEffect(() => {
    getProjectFootprint(project)
      .then((all) => setRows(all.filter((r) => r.cloud === cloud)))
      .catch(() => setRows([]));
  }, [project, cloud]);
  return rows;
}
