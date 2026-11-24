"use client";

import { StatTile, type StatTone } from "@/components/data/StatTile";
import { useObsFormat } from "@/components/observability/shared";
import type { AppSummary } from "@/lib/api";
import { useTranslations } from "next-intl";
import { relativeAge } from "./format";
import { bytesLabel } from "./shared";

const GRID = "grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5";

function ratioTone(ratio: number | undefined, warn: number, bad: number): StatTone | undefined {
  if (ratio === undefined) return undefined;
  if (ratio >= bad) return "bad";
  if (ratio >= warn) return "warn";
  return undefined;
}

const defined = (values: (number | null)[] | undefined) => values ?? [];

/** Five tiles answering "is it fine": web apps and workers. */
export function WebStatusBand({ summary, loading }: { summary: AppSummary | null; loading: boolean }) {
  const t = useTranslations("appOverview.tiles");
  const { number, percent, ms } = useObsFormat();
  const tiles = summary?.tiles;
  const series = summary?.series;
  const requests = tiles?.requests_total;
  const noTraffic = requests === 0;
  const availability = tiles?.availability_pct;
  const restarts = tiles?.restarts ?? 0;
  const memRatio = tiles?.mem_ratio;
  return (
    <div className={GRID}>
      <StatTile
        label={t("availability")}
        loading={loading}
        value={availability === undefined ? null : percent(availability / 100, 2)}
        tone={availability === undefined ? undefined : availability < 95 ? "bad" : availability < 99 ? "warn" : "ok"}
        hint={
          noTraffic
            ? t("noTraffic")
            : requests !== undefined
              ? t("availabilityHint", { errors: number(tiles?.errors_5xx ?? 0, 0), requests: number(requests, 0) })
              : undefined
        }
      />
      <StatTile
        label={t("requests")}
        loading={loading}
        value={requests === undefined ? null : number(requests, 0)}
        sparkline={defined(series?.rps)}
        hint={requests ? t("requestsHint", { rate: number(requests / 86_400, 3) }) : noTraffic ? t("noTraffic") : undefined}
      />
      <StatTile
        label={t("errors")}
        loading={loading}
        value={tiles?.errors_5xx === undefined ? null : number(tiles.errors_5xx, 0)}
        sparkline={defined(series?.err_rps)}
        tone={ratioTone(tiles?.error_pct, 1, 5)}
        hint={tiles?.error_pct === undefined ? undefined : t("errorsHint", { pct: percent(tiles.error_pct / 100, 2) })}
      />
      <StatTile
        label={t("latency")}
        loading={loading}
        value={tiles?.p95_ms === undefined ? null : ms(tiles.p95_ms)}
        sparkline={defined(series?.p95_ms)}
        hint={tiles?.p50_ms === undefined ? undefined : t("latencyHint", { value: ms(tiles.p50_ms) })}
      />
      <StatTile
        label={t("restarts")}
        loading={loading}
        value={tiles ? number(restarts, 0) : null}
        tone={restarts >= 3 ? "warn" : undefined}
        hint={
          tiles
            ? memRatio === undefined
              ? t("restartsHint", { ready: tiles.replicas_ready, desired: tiles.replicas_desired })
              : t("restartsMemHint", {
                  ready: tiles.replicas_ready,
                  desired: tiles.replicas_desired,
                  pct: percent(memRatio, 0),
                })
            : t("restartsHint", { ready: 0, desired: 0 })
        }
      />
    </div>
  );
}

/** Datastores have no HTTP traffic: resources, disk, stability and backups instead. */
export function DatastoreStatusBand({ summary, loading }: { summary: AppSummary | null; loading: boolean }) {
  const t = useTranslations("appOverview.tiles");
  const { number, percent, time, locale } = useObsFormat();
  const tiles = summary?.tiles;
  const series = summary?.series;
  const disk = tiles?.disk_bytes;
  const capacity = tiles?.disk_capacity_bytes;
  const diskRatio = disk !== undefined && capacity ? disk / capacity : undefined;
  const backupAt = tiles?.last_backup_at;
  return (
    <div className={GRID}>
      <StatTile
        label={t("cpu")}
        loading={loading}
        value={tiles?.cpu_ratio === undefined ? null : percent(tiles.cpu_ratio, 0)}
        sparkline={defined(series?.cpu_cores)}
        tone={ratioTone(tiles?.cpu_ratio, 0.9, 1.5)}
        hint={
          tiles?.cpu_cores !== undefined && tiles.cpu_request_cores !== undefined
            ? t("cpuHint", { used: number(tiles.cpu_cores, 2), request: number(tiles.cpu_request_cores, 2) })
            : undefined
        }
      />
      <StatTile
        label={t("memory")}
        loading={loading}
        value={tiles?.mem_ratio === undefined ? null : percent(tiles.mem_ratio, 0)}
        sparkline={defined(series?.mem_bytes)}
        tone={ratioTone(tiles?.mem_ratio, 0.8, 0.95)}
        hint={
          tiles?.mem_bytes !== undefined && tiles.mem_limit_bytes !== undefined
            ? t("memoryHint", { used: bytesLabel(tiles.mem_bytes), limit: bytesLabel(tiles.mem_limit_bytes) })
            : undefined
        }
      />
      <StatTile
        label={t("disk")}
        loading={loading}
        value={disk === undefined ? null : bytesLabel(disk)}
        tone={ratioTone(diskRatio, 0.8, 0.95)}
        hint={
          diskRatio !== undefined && capacity
            ? t("diskHint", { pct: percent(diskRatio, 0), capacity: bytesLabel(capacity) })
            : tiles
              ? t("noVolume")
              : undefined
        }
      />
      <StatTile
        label={t("restarts")}
        loading={loading}
        value={tiles ? number(tiles.restarts, 0) : null}
        tone={(tiles?.restarts ?? 0) >= 3 ? "warn" : undefined}
        hint={tiles ? t("restartsHint", { ready: tiles.replicas_ready, desired: tiles.replicas_desired }) : undefined}
      />
      <StatTile
        label={t("lastBackup")}
        loading={loading}
        value={backupAt ? relativeAge(backupAt, locale) : null}
        hint={backupAt ? time(backupAt) : tiles ? t("noBackup") : undefined}
        tone={backupAt ? undefined : tiles ? "warn" : undefined}
      />
    </div>
  );
}
