"use client";

import { StatusDot, STATUS_TEXT, useReasonLabel } from "@/components/app-overview/shared";
import { relativeAge } from "@/components/app-overview/format";
import { appKind } from "@/components/cluster/AppChip";
import { Sparkline } from "@/components/data/Sparkline";
import { EmptyState } from "@/components/data/EmptyState";
import { useObsFormat } from "@/components/observability/shared";
import { Badge } from "@/components/ui/badge";
import { kindLabel, matchesKind, sortByAttention, type KindFilter } from "@/lib/appHealth";
import type { AppHealthRow, AppHealthStatus, AppSummaryKind } from "@/lib/api";
import { prefetchAppSummary } from "@/lib/appSummary";
import { cn } from "@/lib/utils";
import type { Deployment } from "@/types";
import { ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

const COLUMNS =
  "grid grid-cols-[minmax(0,1.6fr)_repeat(2,minmax(0,0.8fr))_14px] md:grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,0.8fr))_minmax(0,1.2fr)_minmax(0,1fr)_14px] items-center gap-2.5 px-3";
const COLLAPSE_AFTER = 8;
const SHOWN_WHEN_COLLAPSED = 6;

const IN_PROGRESS = new Set(["pending", "initializing", "planning", "deploying"]);

interface Row {
  app: Deployment;
  health: AppHealthRow | null;
  name: string;
  kind: AppSummaryKind;
  status: AppHealthStatus;
}

function guessKind(app: Deployment): AppSummaryKind {
  const kind = appKind(app.name, app.template_id, app.template_category);
  if (kind === "db") return "datastore";
  if (kind === "worker") return "worker";
  return "web";
}

function fallbackStatus(app: Deployment): AppHealthStatus {
  if (app.status === "failed") return "down";
  if (app.status === "degraded") return "degraded";
  return "unknown";
}

export function AppsList({
  apps,
  health,
  loading,
  projectName,
  live,
}: {
  apps: Deployment[];
  health: AppHealthRow[] | null;
  loading: boolean;
  projectName: string;
  live?: React.ReactNode;
}) {
  const t = useTranslations("projectHome.apps");
  const [filter, setFilter] = useState<KindFilter>("all");
  const [expanded, setExpanded] = useState(false);

  const byId = new Map((health ?? []).map((h) => [h.id, h]));
  const rows: Row[] = sortByAttention(
    apps
      .filter((app) => app.status !== "deleted")
      .map((app) => {
        const h = byId.get(app.id) ?? null;
        return {
          app,
          health: h,
          name: app.name,
          kind: h?.kind ?? guessKind(app),
          status: h?.status ?? fallbackStatus(app),
        };
      }),
  ).filter((row) => matchesKind(row.kind, filter));

  const collapsible = !expanded && rows.length > COLLAPSE_AFTER;
  const attention = rows.filter((r) => r.status !== "healthy");
  const shownCount = collapsible ? Math.max(SHOWN_WHEN_COLLAPSED, attention.length) : rows.length;
  const shown = rows.slice(0, shownCount);
  const hidden = rows.slice(shownCount);

  return (
    <section aria-labelledby="apps-title" className="rounded-xl border bg-card">
      <header className="flex flex-wrap items-center justify-between gap-2 px-3 pt-3 pb-1.5">
        <div className="flex items-center gap-2">
          <h2 id="apps-title" className="text-sm font-semibold">
            {t("title")}
          </h2>
          {live}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>{t("sort")}</span>
          <select
            aria-label={t("filter")}
            value={filter}
            onChange={(e) => setFilter(e.target.value as KindFilter)}
            className="h-7 rounded-lg border border-input bg-background px-2 text-xs text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="all">{t("all")}</option>
            <option value="web">{t("web")}</option>
            <option value="datastore">{t("datastore")}</option>
            <option value="worker">{t("worker")}</option>
          </select>
        </div>
      </header>
      <div
        className={cn(COLUMNS, "border-b py-1.5 text-[11px] font-semibold text-muted-foreground")}
        aria-hidden
      >
        <span>{t("app")}</span>
        <span>{t("rps")}</span>
        <span>{t("errors")}</span>
        <span className="hidden md:block">{t("p95")}</span>
        <span className="hidden md:block">{t("spark")}</span>
        <span className="hidden md:block">{t("lastDeploy")}</span>
        <span />
      </div>
      <ul>
        {loading && apps.length === 0
          ? Array.from({ length: 4 }, (_, i) => (
              <li key={i} className={cn(COLUMNS, "border-b py-2.5 last:border-0")} aria-busy>
                <span className="h-4 w-32 animate-pulse rounded bg-muted" />
                <span className="h-4 w-10 animate-pulse rounded bg-muted" />
                <span className="h-4 w-10 animate-pulse rounded bg-muted" />
                <span className="hidden h-4 w-10 animate-pulse rounded bg-muted md:block" />
                <span className="hidden h-5 animate-pulse rounded bg-muted md:block" />
                <span className="hidden h-4 w-16 animate-pulse rounded bg-muted md:block" />
                <span />
              </li>
            ))
          : shown.map((row) => (
              <li key={row.app.id} className="border-b last:border-0">
                <AppRow row={row} projectName={projectName} valuesLoading={loading && !row.health} />
              </li>
            ))}
        {!loading && rows.length === 0 && filter !== "all" && (
          <li className="p-3">
            <EmptyState compact title={t("noMatch")} />
          </li>
        )}
        {hidden.length > 0 && (
          <li>
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="w-full px-3 py-2.5 text-left text-xs text-muted-foreground hover:bg-muted/50"
            >
              {t("more", { count: hidden.length, names: hidden.map((r) => r.name).join(", ") })}
            </button>
          </li>
        )}
      </ul>
    </section>
  );
}

function AppRow({
  row,
  projectName,
  valuesLoading,
}: {
  row: Row;
  projectName: string;
  valuesLoading: boolean;
}) {
  const t = useTranslations("projectHome.apps");
  const reasonLabel = useReasonLabel();
  const { number, percent, ms, locale } = useObsFormat();
  const { app, health, status } = row;
  const datastore = row.kind === "datastore";
  const kind = kindLabel(row.kind, app.name, app.template_id, app.template_name);
  const progress = IN_PROGRESS.has(app.status)
    ? t("deploying")
    : app.status === "deleting"
      ? t("deleting")
      : app.status === "failed"
        ? t("failed")
        : null;
  const reason = progress ?? (status !== "healthy" ? reasonLabel(health?.status_reason) : null);
  const ratio = (value: number | undefined) => (value === undefined ? "-" : percent(value, 0));
  const cells = datastore
    ? [
        health?.cpu_ratio === undefined ? "-" : t("cpu", { pct: ratio(health.cpu_ratio) }),
        health?.mem_ratio === undefined ? "-" : t("mem", { pct: ratio(health.mem_ratio) }),
        health?.disk_ratio === undefined ? "-" : t("disk", { pct: ratio(health.disk_ratio) }),
      ]
    : [
        health?.rps === undefined ? "-" : number(health.rps, 2),
        health?.error_pct === undefined ? "-" : percent(health.error_pct / 100, 1),
        health?.p95_ms === undefined ? "-" : ms(health.p95_ms),
      ];
  const errorsHigh = !datastore && (health?.error_pct ?? 0) >= 1;
  const skeleton = <span className="h-4 w-10 animate-pulse rounded bg-muted" />;
  return (
    <Link
      href={`/projects/${encodeURIComponent(projectName)}/apps/${app.id}`}
      title={t("open", { name: app.name })}
      onMouseEnter={() => prefetchAppSummary(app.id)}
      onFocus={() => prefetchAppSummary(app.id)}
      className={cn(
        COLUMNS,
        "group py-2.5 text-[12.5px] transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <StatusDot status={status} />
        <b className="truncate font-semibold">{app.name}</b>
        <span className="shrink-0 rounded border px-1.5 text-[10.5px] text-muted-foreground">
          {t(`kinds.${kind}`)}
        </span>
        {reason && (
          <Badge variant="outline" className={cn("hidden shrink truncate px-1.5 py-0 text-[11px] sm:inline-flex", STATUS_TEXT[status])}>
            {reason}
          </Badge>
        )}
      </span>
      {valuesLoading ? skeleton : <span className="tabular-nums">{cells[0]}</span>}
      {valuesLoading ? (
        skeleton
      ) : (
        <span className={cn("tabular-nums", errorsHigh && "font-semibold text-destructive")}>{cells[1]}</span>
      )}
      <span className="hidden tabular-nums md:block">{valuesLoading ? skeleton : cells[2]}</span>
      <span className="hidden md:block">
        <Sparkline
          values={health?.spark ?? []}
          loading={valuesLoading}
          label={t("sparkLabel", { name: app.name })}
          color={status === "down" || status === "degraded" ? "var(--destructive)" : datastore ? "var(--chart-2)" : undefined}
          className="h-6 w-full"
        />
      </span>
      <span className="hidden truncate text-xs text-muted-foreground md:block">
        {health?.last_deploy ? `${health.last_deploy.version} · ${relativeAge(health.last_deploy.at, locale)}` : valuesLoading ? skeleton : "-"}
      </span>
      <ChevronRight aria-hidden className="size-3.5 text-muted-foreground group-hover:text-primary" />
    </Link>
  );
}
