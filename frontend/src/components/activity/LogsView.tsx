"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  getLogTargets,
  getProjectLogs,
  isNotConnected,
  searchObsLogs,
  type AppEnv,
} from "@/lib/api";
import { NotConnected } from "@/components/clusters/NotConnected";
import { TopErrors } from "@/components/observability/TopErrors";
import { useObsCapabilities } from "@/components/observability/shared";
import { cn } from "@/lib/utils";
import type { LogFilters, LogTargets } from "@/types";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  Download,
  ExternalLink,
  Link2,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RefreshCw,
  Search,
  Terminal,
  X,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { FilterBar } from "./FilterBar";
import { errorMessage } from "./labels";
import { LogPane, colourOf, rowKey, severity, streamOf, type Row } from "./LogPane";
import { NativeSelect } from "./NativeSelect";
import { NetworkPanel } from "./NetworkPanel";
import { VolumeCard } from "./VolumeCard";
import { matches, searchPattern } from "./logHighlight";
import { logsQueryToParams, type LogsQuery } from "./logsQuery";
import { useDeploymentStream } from "./useDeploymentStream";
import { pollWhileVisible } from "@/lib/visiblePolling";

const LIVE_MS = 3000;
const CONTEXT_MS = 60_000;
const CONTEXT_LINES = 20;
const MAX_LINES = 3000;
const COMPACT_LINES = 50;
// Backend bounds: the store search answers at most 500 lines, both searches take 200 chars.
const OBS_LINES = 500;
const MAX_SEARCH = 200;
// The store has no `since`: the live poll re-reads its smallest range
// (LOG_RANGE in the backend's app_observability.py).
const OBS_LIVE_RANGE = "15m";

const PERIODS: { value: string; ms: number }[] = [
  { value: "15m", ms: 15 * 60_000 },
  { value: "1h", ms: 3_600_000 },
  { value: "6h", ms: 6 * 3_600_000 },
  { value: "24h", ms: 24 * 3_600_000 },
  { value: "7d", ms: 7 * 24 * 3_600_000 },
];

type Level = NonNullable<LogFilters["level"]>;

interface QueryProps {
  variant?: "compact" | "full";
  project: string;
  /** Pins the view to one app's namespace (the app page). */
  namespace?: string;
  /** The app these logs belong to: its observability store is read when it has one. */
  appId?: number;
  /** Which environment of the app's store is read (prod by default). */
  env?: AppEnv;
  /** Lines of one trace (the Logs tab's ?trace=). */
  trace?: string | null;
  onClearTrace?: () => void;
  /** Fills the page height (the project logs page). */
  fullPage?: boolean;
  /** Filters to start from, as a copied link carries them. */
  initialQuery?: LogsQuery;
}

interface StreamProps {
  variant: "stream";
  deploymentId: number;
  deploymentStatus: string;
}

export type LogsViewProps = QueryProps | StreamProps;

/**
 * THE log component. `compact` is the last lines, live, without filters (cards);
 * `full` has every filter (Logs tab, project logs); `stream` is a deployment's
 * console. Compact and stream open the full layout in a full-screen dialog.
 */
export function LogsView(props: LogsViewProps) {
  if (props.variant === "stream") return <StreamLogs {...props} />;
  // The filters seed their namespace from the props once: a new env starts afresh.
  return <QueryLogs key={props.env} {...props} />;
}

function LogsDialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="top-0 left-0 flex h-dvh w-screen max-w-none translate-x-0 translate-y-0 flex-col rounded-none p-4 sm:max-w-none"
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        {children}
      </DialogContent>
    </Dialog>
  );
}

interface LevelCounts {
  all: number;
  error: number;
  warn: number;
  info: number;
  debug: number;
}

function countLevels(rows: Row[]): LevelCounts {
  const counts = { all: rows.length, error: 0, warn: 0, info: 0, debug: 0 };
  for (const row of rows) {
    const level = severity(row);
    if (level) counts[level] += 1;
    else if (row.level?.toLowerCase() === "debug") counts.debug += 1;
    else counts.info += 1;
  }
  return counts;
}

const LEVEL_TONE: Record<Level, string> = {
  error: "text-red-700 dark:text-red-400",
  warn: "text-amber-700 dark:text-amber-400",
  info: "",
  debug: "text-muted-foreground",
};

/** All, Error, Warn, Info with their counts; a level the source cannot filter is shown, not offered. */
function LevelChips({
  counts,
  active,
  pickable,
  onPick,
  serverFiltered = true,
}: {
  counts: LevelCounts;
  active: Level | null;
  pickable: Level[];
  onPick: (level: Level | null) => void;
  /** The level filter is applied by the source, so only that level is loaded. */
  serverFiltered?: boolean;
}) {
  const t = useTranslations("activity.logs.levels");
  const levels: Level[] = ["error", "warn", "info", ...(pickable.includes("debug") ? ["debug" as const] : [])];
  const chip = (selected: boolean) =>
    cn(
      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs",
      selected ? "border-primary bg-primary/10 font-medium" : "hover:bg-muted",
    );
  // A server-side level filter leaves only that level loaded: other counts would read 0.
  const count = (level: Level | "all") =>
    serverFiltered && active && level !== active ? null : counts[level];
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("label")}>
      <button type="button" className={chip(active === null)} onClick={() => onPick(null)}>
        {t("all")}
        {count("all") !== null && (
          <span className="tabular-nums text-muted-foreground">{counts.all}</span>
        )}
      </button>
      {levels.map((level) => {
        const n = count(level);
        const body = (
          <>
            <span className={LEVEL_TONE[level]}>{t(level)}</span>
            {n !== null && <span className="tabular-nums text-muted-foreground">{n}</span>}
          </>
        );
        return pickable.includes(level) ? (
          <button
            key={level}
            type="button"
            className={chip(active === level)}
            onClick={() => onPick(active === level ? null : level)}
          >
            {body}
          </button>
        ) : (
          <span key={level} className="inline-flex items-center gap-1.5 px-2.5 py-0.5 text-xs">
            {body}
          </span>
        );
      })}
    </div>
  );
}

/** The text search with its regex toggle; the regex is applied to the loaded lines. */
function SearchBox({
  value,
  onChange,
  onSubmit,
  regex,
  onRegex,
  invalid,
  matchCount,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  regex: boolean;
  onRegex: (v: boolean) => void;
  invalid: boolean;
  matchCount: number | null;
  className?: string;
}) {
  const t = useTranslations("activity.logs");
  return (
    <form
      className={cn("flex min-w-0 items-center gap-1", className)}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.();
      }}
    >
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={MAX_SEARCH}
        placeholder={t("search")}
        aria-label={t("searchButton")}
        aria-invalid={invalid || undefined}
        className="h-8 min-w-0 flex-1 font-mono"
      />
      <Button
        type="button"
        variant={regex ? "secondary" : "ghost"}
        size="sm"
        aria-pressed={regex}
        onClick={() => onRegex(!regex)}
        title={t("regex")}
        className="font-mono"
      >
        .*
      </Button>
      {onSubmit && (
        <Button type="submit" variant="ghost" size="sm" title={t("searchButton")}>
          <Search className="h-4 w-4" />
        </Button>
      )}
      {invalid ? (
        <span className="shrink-0 text-xs text-destructive">{t("invalidRegex")}</span>
      ) : (
        matchCount !== null && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {t("matchCount", { count: matchCount })}
          </span>
        )
      )}
    </form>
  );
}

function downloadText(text: string, name: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/** Escape closes the innermost thing first: a line's details, then the context view. */
function useEscape(handler: (() => boolean) | null) {
  useEffect(() => {
    if (!handler) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest("select,[role=menu],[role=listbox]")) return;
      // Handled here, so the enclosing dialog does not close as well.
      if (handler()) e.stopPropagation();
    };
    // Capture: the dialog listens on the document and would close first.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [handler]);
}

function QueryLogs({
  variant = "full",
  project,
  namespace,
  appId,
  env = "prod",
  trace = null,
  onClearTrace,
  fullPage = false,
  initialQuery,
  onExit,
}: QueryProps & {
  /** Set inside the full-screen dialog: the view fills it and offers to leave. */
  onExit?: () => void;
}) {
  const compact = variant === "compact";
  const [targets, setTargets] = useState<LogTargets | null>(null);
  const [filters, setFilters] = useState<LogFilters>({
    namespace: namespace ?? null,
    pod: initialQuery?.pod ?? null,
    container: initialQuery?.container ?? null,
    level: initialQuery?.level ?? null,
    search: initialQuery?.q ?? null,
  });
  const [searchInput, setSearchInput] = useState(initialQuery?.q ?? "");
  const [regex, setRegex] = useState(initialQuery?.regex ?? false);
  // A trace link comes from the trace list, whose longest range is 24 h
  // (TRACE_RANGE in the backend's app_observability.py).
  const [period, setPeriod] = useState(initialQuery?.period ?? (trace ? "24h" : "1h"));
  const [lines, setLines] = useState<Row[] | null>(null);
  const [live, setLive] = useState(compact);
  const [wrap, setWrap] = useState(true);
  const [showTime, setShowTime] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [context, setContext] = useState<{ anchor: string; lines: Row[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const newest = useRef<string | null>(null);
  // Answers of an older load or poll are dropped: they match older filters.
  const generation = useRef(0);
  const polling = useRef(false);
  const top = useRef<HTMLDivElement>(null);
  const t = useTranslations("activity");
  const caps = useObsCapabilities(appId ?? 0, appId != null);
  const obs = appId != null && caps.data?.logs === true;
  // A failed capabilities request means the activity logs.
  const ready = appId == null || !caps.loading;
  // A regex runs on the loaded lines: the stores only know phrases.
  const serverSearch = regex ? null : filters.search;
  // The activity logs have no trace field: the trace id is searched as text.
  const term = serverSearch ?? trace;
  const { pattern, invalid } = searchPattern(filters.search ?? (regex ? null : trace), regex);

  useEffect(() => {
    if (compact || obs || !ready) return;
    getLogTargets(project).then(setTargets, (e) => setError(e));
  }, [project, compact, obs, ready]);

  /** Newest first from either store: the selected period, or the live window. */
  const fetchRows = useCallback(
    async (follow: boolean): Promise<Row[]> => {
      const limit = compact ? COMPACT_LINES : follow ? 300 : obs ? OBS_LINES : 1000;
      if (!obs || appId == null) {
        const ms = PERIODS.find((p) => p.value === period)?.ms ?? 3_600_000;
        const since = follow
          ? (newest.current ?? new Date(Date.now() - LIVE_MS).toISOString())
          : new Date(Date.now() - ms).toISOString();
        return getProjectLogs(project, { ...filters, search: term, since, limit });
      }
      const found = await searchObsLogs(appId, {
        range: follow ? OBS_LIVE_RANGE : period,
        level: filters.level ?? undefined,
        q: serverSearch ?? undefined,
        trace_id: trace ?? undefined,
        limit,
        env,
      });
      return found.map((r) => ({
        time: r.time,
        namespace: namespace ?? "",
        pod: r.pod,
        container: r.container,
        line: r.message,
        level: r.level,
        trace_id: r.trace_id,
      }));
    },
    [obs, appId, env, project, filters, term, serverSearch, trace, period, namespace, compact],
  );

  const load = useCallback(async () => {
    const id = ++generation.current;
    setLoading(true);
    setContext(null);
    setOpenKey(null);
    try {
      const result = await fetchRows(false);
      if (id !== generation.current) return;
      // The API answers newest first; a terminal reads oldest first.
      setLines([...result].reverse());
      newest.current = result[0]?.time ?? null;
      setError(null);
    } catch (e) {
      if (id === generation.current) setError(e);
    } finally {
      if (id === generation.current) setLoading(false);
    }
  }, [fetchRows]);

  useEffect(() => {
    if (ready) load();
  }, [load, ready]);

  useEffect(() => {
    if (!live || !ready) return;
    return pollWhileVisible(async () => {
      // A slow store must not pile up queries: skip the tick while one runs.
      if (polling.current) return;
      polling.current = true;
      const id = generation.current;
      try {
        const fresh = await fetchRows(true);
        if (id !== generation.current || fresh.length === 0) return;
        // The store re-reads a whole window: only unseen lines, none older
        // than the newest shown, so every line appears once and in order.
        const from = newest.current ? Date.parse(newest.current) : 0;
        setLines((current) => {
          const seen = new Set((current ?? []).map(rowKey));
          const added = [...fresh]
            .reverse()
            .filter((l) => !seen.has(rowKey(l)) && Date.parse(l.time) >= from);
          return [...(current ?? []), ...added].slice(
            -(compact ? COMPACT_LINES : MAX_LINES),
          );
        });
        if (Date.parse(fresh[0].time) >= from) newest.current = fresh[0].time;
        setError(null);
      } catch (e) {
        if (id !== generation.current) return;
        setError(e);
        // The compact card has no Follow button: it keeps retrying instead.
        if (!compact) setLive(false);
      } finally {
        polling.current = false;
      }
    }, LIVE_MS);
  }, [live, ready, fetchRows, compact]);

  const escape = useCallback(() => {
    if (openKey) setOpenKey(null);
    else if (context) setContext(null);
    else if (fullscreen) setFullscreen(false);
    else return false;
    return true;
  }, [openKey, context, fullscreen]);
  useEscape(compact ? null : escape);

  const showContext = async (anchor: Row) => {
    const k = rowKey(anchor);
    const around = (all: Row[]) => {
      const i = all.findIndex((l) => rowKey(l) === k);
      return i < 0 ? all : all.slice(Math.max(0, i - CONTEXT_LINES), i + CONTEXT_LINES + 1);
    };
    // The store cannot read a window around a time: the loaded lines stand in.
    if (obs) {
      setContext({ anchor: k, lines: around(lines ?? []) });
      setOpenKey(null);
      return;
    }
    const at = new Date(anchor.time).getTime();
    try {
      const found = await getProjectLogs(project, {
        namespace: anchor.namespace,
        pod: anchor.pod,
        container: anchor.container,
        since: new Date(at - CONTEXT_MS).toISOString(),
        until: new Date(at + CONTEXT_MS).toISOString(),
        limit: 300,
      });
      setContext({ anchor: k, lines: around([...found].reverse()) });
      setOpenKey(null);
    } catch (e) {
      setError(e);
    }
  };

  const set = (patch: Partial<LogFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const source = context ? context.lines : lines;

  const streams = useMemo(() => {
    const counts = new Map<string, number>();
    for (const l of lines ?? []) {
      const s = streamOf(l, project);
      counts.set(s, (counts.get(s) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [lines, project]);

  const shown = useMemo(() => {
    if (source === null) return null;
    return source.filter(
      (l) =>
        !hidden.has(streamOf(l, project)) &&
        // The context view shows the neighbours whatever they contain.
        (!regex || !pattern || context !== null || matches(l.line, pattern)),
    );
  }, [source, hidden, project, regex, pattern, context]);

  const counts = useMemo(() => countLevels(lines ?? []), [lines]);
  const matchCount =
    pattern && shown ? shown.filter((l) => matches(l.line, pattern)).length : null;

  const query: LogsQuery = {
    period,
    level: filters.level ?? undefined,
    q: filters.search ?? undefined,
    regex,
    pod: filters.pod ?? undefined,
    container: filters.container ?? undefined,
  };
  const linkTo = () => {
    const params = new URLSearchParams();
    let path = `/projects/${encodeURIComponent(project)}/logs`;
    if (appId != null) path = `/projects/${encodeURIComponent(project)}/apps/${appId}/logs`;
    else if (namespace || filters.namespace)
      params.set("namespace", namespace ?? filters.namespace ?? "");
    if (trace) params.set("trace", trace);
    if (env === "staging") params.set("env", env);
    logsQueryToParams(query, params);
    const qs = params.toString();
    return `${window.location.origin}${path}${qs ? `?${qs}` : ""}`;
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(linkTo());
      toast.success(t("logs.details.linkCopied"));
    } catch {
      toast.error(linkTo());
    }
  };

  const download = () =>
    downloadText(
      (shown ?? []).map((l) => `${l.time} ${streamOf(l, project)} | ${l.line}`).join("\n"),
      `logs-${project}.log`,
    );

  const toggleStream = (s: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  const filled = fullscreen || fullPage || onExit !== undefined;
  const tab = new URLSearchParams();
  if (namespace) tab.set("namespace", namespace);
  // The new tab reads the same store as this one.
  if (appId != null) tab.set("app", String(appId));
  if (env === "staging") tab.set("env", env);
  const tabQuery = tab.toString();
  const errorBox =
    error != null &&
    (isNotConnected(error) ? (
      <NotConnected cloud={isNotConnected(error)!.cloud} />
    ) : (
      <p className="flex items-center gap-2 text-sm text-destructive">
        <AlertTriangle className="h-4 w-4" /> {errorMessage(error)}
      </p>
    ));

  if (compact) {
    return (
      <div className="flex flex-col gap-2">
        {errorBox}
        <div className="relative">
          <LogPane
            rows={shown}
            project={project}
            appId={appId}
            pattern={pattern}
            wrap
            showTime
            className="h-64"
            emptyText={t("logs.noLines")}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExpanded(true)}
            className="absolute top-2 right-4 bg-background/90"
          >
            <Maximize2 className="h-3.5 w-3.5" /> {t("logs.expand")}
          </Button>
        </div>
        {expanded && (
          <LogsDialog open onClose={() => setExpanded(false)} title={t("logs.dialogTitle")}>
            <QueryLogs
              project={project}
              namespace={namespace}
              appId={appId}
              env={env}
              trace={trace}
              initialQuery={initialQuery}
              onExit={() => setExpanded(false)}
            />
          </LogsDialog>
        )}
      </div>
    );
  }

  const levelPickable: Level[] = obs ? ["error", "warn", "info", "debug"] : ["error", "warn"];

  return (
    <div
      ref={top}
      className={cn(
        "flex flex-col gap-3",
        fullscreen && "fixed inset-0 z-50 bg-background p-4",
        fullPage && !fullscreen && "h-[calc(100vh-8rem)]",
        onExit && "h-full min-h-0",
      )}
    >
      <FilterBar
        selects={
          <>
            {!namespace && !obs && (
              <NativeSelect
                aria-label={t("logs.aria.namespace")}
                value={filters.namespace ?? ""}
                onChange={(v) => set({ namespace: v || null, pod: null })}
              >
                <option value="">{t("logs.allNamespaces")}</option>
                {targets?.namespaces.map((n) => (
                  <option key={n} value={n}>
                    {n.replace(`${project}-`, "")}
                  </option>
                ))}
              </NativeSelect>
            )}
            {!obs && (
              <>
                <NativeSelect
                  aria-label={t("logs.aria.pod")}
                  value={filters.pod ?? ""}
                  onChange={(v) => set({ pod: v || null })}
                >
                  <option value="">{t("logs.allPods")}</option>
                  {targets?.pods
                    .filter((p) => !filters.namespace || p.startsWith(filters.namespace))
                    .map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                </NativeSelect>
                <NativeSelect
                  aria-label={t("logs.aria.container")}
                  value={filters.container ?? ""}
                  onChange={(v) => set({ container: v || null })}
                >
                  <option value="">{t("logs.allContainers")}</option>
                  {targets?.containers.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </NativeSelect>
              </>
            )}
            <NativeSelect aria-label={t("logs.aria.period")} value={period} onChange={setPeriod}>
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {t(`periods.${p.value}`)}
                </option>
              ))}
            </NativeSelect>
            <SearchBox
              // The store has no pod filters: the phrase takes their room.
              className={cn("col-span-2", obs ? "md:col-span-2 xl:col-span-5" : "md:col-span-3 xl:col-span-2")}
              value={searchInput}
              onChange={setSearchInput}
              onSubmit={() => set({ search: searchInput.trim() || null })}
              regex={regex}
              onRegex={setRegex}
              invalid={invalid}
              matchCount={matchCount}
            />
          </>
        }
        toggles={
          <>
            <LevelChips
              counts={counts}
              active={filters.level ?? null}
              pickable={levelPickable}
              onPick={(level) => set({ level })}
            />
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={wrap} onChange={(e) => setWrap(e.target.checked)} />
              {t("logs.lineWrap")}
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showTime}
                onChange={(e) => setShowTime(e.target.checked)}
              />
              {t("logs.timestamp")}
            </label>
          </>
        }
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={load}
              disabled={loading}
              title={t("common.refresh")}
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            </Button>
            <Button variant="ghost" size="sm" onClick={download} title={t("logs.download")}>
              <Download className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={copyLink} title={t("logs.details.copyLink")}>
              <Link2 className="h-4 w-4" />
            </Button>
            {!fullPage && !onExit && (
              <Link
                href={`/projects/${encodeURIComponent(project)}/logs${tabQuery ? `?${tabQuery}` : ""}`}
                target="_blank"
                rel="noopener noreferrer"
                title={t("logs.openNewTab")}
                className="inline-flex h-8 items-center rounded-md px-2.5 hover:bg-muted"
              >
                <ExternalLink className="h-4 w-4" />
              </Link>
            )}
            {onExit ? (
              <Button variant="ghost" size="sm" onClick={onExit} title={t("logs.exitFullscreen")}>
                <Minimize2 className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setFullscreen((v) => !v)}
                title={fullscreen ? t("logs.exitFullscreen") : t("logs.fullscreen")}
              >
                {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
              </Button>
            )}
            <Button
              variant={live ? "default" : "outline"}
              size="sm"
              onClick={() => setLive((v) => !v)}
            >
              {live ? <Pause className="mr-1.5 h-4 w-4" /> : <Play className="mr-1.5 h-4 w-4" />}
              {t(live ? "logs.live" : "logs.follow")}
            </Button>
          </>
        }
      />

      {!fullscreen && <VolumeCard project={project} namespace={namespace} />}

      {streams.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {streams.map(([s, count]) => (
            <button
              key={s}
              type="button"
              onClick={() => toggleStream(s)}
              title={t(hidden.has(s) ? "logs.showStream" : "logs.hideStream")}
              className={cn(
                "rounded-full border px-2.5 py-0.5 font-mono text-xs transition-opacity",
                colourOf(s),
                hidden.has(s) && "opacity-40 line-through",
              )}
            >
              {s} · {count}
            </button>
          ))}
        </div>
      )}

      {errorBox}

      {/* On the project logs a typed phrase replaces the trace id as the search. */}
      {trace && (obs || !filters.search) && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-sm">
          <span>{t("logs.traceFilter", { id: trace.slice(0, 12) })}</span>
          {onClearTrace && (
            <Button variant="outline" size="sm" onClick={onClearTrace}>
              <X className="mr-1.5 h-4 w-4" />
              {t("logs.clearTrace")}
            </Button>
          )}
        </div>
      )}

      {context && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-sm">
          <span>{t(obs ? "logs.contextBannerLoaded" : "logs.contextBanner")}</span>
          <Button variant="outline" size="sm" onClick={() => setContext(null)}>
            <ArrowLeft className="mr-1.5 h-4 w-4" />
            {t("logs.backToResults")}
            <kbd className="ml-2 rounded border px-1 font-mono text-[10px] text-muted-foreground">
              Esc
            </kbd>
          </Button>
        </div>
      )}

      <LogPane
        rows={shown}
        project={project}
        appId={appId}
        pattern={pattern}
        wrap={wrap}
        showTime={showTime}
        className={filled ? "min-h-0 flex-1" : "h-[36rem]"}
        emptyText={t("logs.noLines")}
        expandable
        openKey={openKey}
        onToggle={(k) => setOpenKey((o) => (o === k ? null : k))}
        anchor={context?.anchor}
        onShowContext={context ? undefined : showContext}
        copyLink={linkTo}
        skeletonLines={20}
      />

      {!fullscreen && !onExit && (
        <>
          {obs && appId != null && (
            <TopErrors
              appId={appId}
              base={`/projects/${encodeURIComponent(project)}/apps/${appId}`}
              range={period}
              env={env}
              onPick={(row) => {
                const words = row.pattern.split("<N>")[0].trim().slice(0, MAX_SEARCH);
                setSearchInput(words);
                setRegex(false);
                set({ level: "error", search: words || null });
                top.current?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            />
          )}
          <NetworkPanel project={project} />
        </>
      )}
    </div>
  );
}

function StreamLogs({ deploymentId, deploymentStatus }: StreamProps) {
  const t = useTranslations("appPage.console");
  const tl = useTranslations("activity.logs");
  const { lines, connected } = useDeploymentStream(deploymentId, deploymentStatus);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [filter, setFilter] = useState("");
  const [regex, setRegex] = useState(false);
  const [level, setLevel] = useState<Level | null>(null);
  const [wrap, setWrap] = useState(true);
  const [openKey, setOpenKey] = useState<string | null>(null);

  const rows = useMemo<Row[]>(
    () =>
      lines.map((line, i) => ({
        id: String(i),
        time: "",
        namespace: "",
        pod: "",
        container: "",
        line,
      })),
    [lines],
  );
  const { pattern, invalid } = searchPattern(filter, regex);
  const counts = useMemo(() => countLevels(rows), [rows]);
  const shown = useMemo(
    () =>
      rows.filter((r) => {
        if (pattern && !matches(r.line, pattern)) return false;
        if (!level) return true;
        const s = severity(r);
        return level === "info" ? s === null : s === level;
      }),
    [rows, pattern, level],
  );

  const escape = useCallback(() => {
    if (!openKey) return false;
    setOpenKey(null);
    return true;
  }, [openKey]);
  useEscape(expanded ? escape : null);

  const download = () =>
    downloadText(lines.join("\n"), `deployment-${deploymentId}-logs.txt`);
  const empty = filter || level ? t("noMatch") : t("empty");
  const title = (
    <span className="flex items-center gap-2 text-sm font-semibold">
      <Terminal className="h-4 w-4 text-green-600 dark:text-green-400" />
      {t("title")}
      <span
        className={cn("h-2 w-2 rounded-full", connected ? "bg-green-600" : "bg-amber-500")}
        title={tl(connected ? "streamConnected" : "streamWaiting")}
      />
    </span>
  );

  return (
    <div className="rounded-xl border bg-card">
      <div className="flex items-center justify-between gap-2 px-4 py-2">
        {title}
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={download}
            disabled={lines.length === 0}
            title={t("download")}
            aria-label={t("download")}
          >
            <Download />
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setExpanded(true)}>
            <Maximize2 className="h-3.5 w-3.5" /> {tl("expand")}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label={open ? t("collapse") : t("expand")}
          >
            <ChevronDown className={cn("transition-transform", open && "rotate-180")} />
          </Button>
        </div>
      </div>
      {open && !expanded && (
        <div className="space-y-2 border-t p-3">
          <SearchBox
            value={filter}
            onChange={setFilter}
            regex={regex}
            onRegex={setRegex}
            invalid={invalid}
            matchCount={pattern ? shown.length : null}
          />
          <LogPane
            rows={shown}
            pattern={pattern}
            wrap
            showTime={false}
            className="h-72"
            emptyText={empty}
          />
        </div>
      )}
      <LogsDialog open={expanded} onClose={() => setExpanded(false)} title={t("title")}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          {title}
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={download} title={t("download")}>
              <Download className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded(false)}
              title={tl("exitFullscreen")}
            >
              <Minimize2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <SearchBox
            className="w-full max-w-xl"
            value={filter}
            onChange={setFilter}
            regex={regex}
            onRegex={setRegex}
            invalid={invalid}
            matchCount={pattern ? shown.length : null}
          />
          <LevelChips
            counts={counts}
            active={level}
            pickable={["error", "warn", "info"]}
            onPick={setLevel}
            serverFiltered={false}
          />
          <label className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={wrap} onChange={(e) => setWrap(e.target.checked)} />
            {tl("lineWrap")}
          </label>
        </div>
        <LogPane
          rows={shown}
          pattern={pattern}
          wrap={wrap}
          showTime={false}
          className="min-h-0 flex-1"
          emptyText={empty}
          expandable
          openKey={openKey}
          onToggle={(k) => setOpenKey((o) => (o === k ? null : k))}
        />
      </LogsDialog>
    </div>
  );
}
