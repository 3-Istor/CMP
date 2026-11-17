"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getLogTargets,
  getProjectLogs,
  isNotConnected,
  searchObsLogs,
} from "@/lib/api";
import { NotConnected } from "@/components/clusters/NotConnected";
import { TopErrors } from "@/components/observability/OverviewObservability";
import { useObsCapabilities } from "@/components/observability/shared";
import { cn } from "@/lib/utils";
import type { LogFilters, LogLine, LogTargets } from "@/types";
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  ExternalLink,
  ListTree,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RefreshCw,
  Search,
  Waypoints,
  X,
} from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FilterBar } from "./FilterBar";
import { errorMessage } from "./labels";
import { NativeSelect } from "./NativeSelect";
import { NetworkPanel } from "./NetworkPanel";
import { VolumeCard } from "./VolumeCard";
import { severityOf, TOKEN_CLASS, tokenize } from "./logHighlight";

const LIVE_MS = 3000;
const CONTEXT_MS = 60_000;
const MAX_LINES = 3000;
const COMPACT_LINES = 50;
// Backend bounds: the store search answers at most 500 lines, both searches take 200 chars.
const OBS_LINES = 500;
const MAX_SEARCH = 200;
// The store has no `since`: the live poll re-reads this window.
const OBS_LIVE_RANGE = "15m";

const PERIODS: { value: string; label: string; ms: number }[] = [
  { value: "15m", label: "15 minutes", ms: 15 * 60_000 },
  { value: "1h", label: "1 heure", ms: 3_600_000 },
  { value: "6h", label: "6 heures", ms: 6 * 3_600_000 },
  { value: "24h", label: "24 heures", ms: 24 * 3_600_000 },
  { value: "7d", label: "7 jours", ms: 7 * 24 * 3_600_000 },
];

/** A line from either store; the observability store also gives level and trace. */
type Row = LogLine & { level?: string | null; trace_id?: string | null };

const LEVELS: Record<string, "error" | "warn"> = {
  error: "error",
  err: "error",
  fatal: "error",
  critical: "error",
  panic: "error",
  warn: "warn",
  warning: "warn",
};

function severity(row: Row): "error" | "warn" | null {
  return row.level ? (LEVELS[row.level.toLowerCase()] ?? null) : severityOf(row.line);
}

// One colour per process, as docker compose does; readable on both themes.
const PALETTE = [
  "text-sky-600 dark:text-sky-400",
  "text-emerald-600 dark:text-emerald-400",
  "text-violet-600 dark:text-violet-400",
  "text-amber-600 dark:text-amber-400",
  "text-pink-600 dark:text-pink-400",
  "text-teal-600 dark:text-teal-400",
  "text-orange-600 dark:text-orange-400",
  "text-indigo-600 dark:text-indigo-400",
  "text-lime-600 dark:text-lime-400",
  "text-rose-600 dark:text-rose-400",
];

/** The process a line comes from: the pod without its generated suffixes. */
function streamOf(l: LogLine, project: string): string {
  const base = l.pod
    .replace(/-[a-z0-9]{8,10}-[a-z0-9]{5}$/, "")
    .replace(/-[a-z0-9]{5}$/, "")
    .replace(`${project}-`, "")
    .replace(/-cnp-generic-app$/, "");
  return l.container &&
    !base.endsWith(l.container) &&
    !l.pod.includes(l.container)
    ? `${base}/${l.container}`
    : base;
}

function colourOf(stream: string): string {
  let hash = 0;
  for (const ch of stream) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

function clock(iso: string, locale: string): string {
  return new Date(iso).toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function key(l: LogLine): string {
  return `${l.time}|${l.pod}|${l.container}|${l.line}`;
}

function Marked({
  text,
  term,
}: {
  text: string;
  term: string | null | undefined;
}) {
  if (!term) return <>{text}</>;
  const parts = text.split(
    new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "i"),
  );
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark
            key={i}
            className="rounded bg-yellow-300/70 px-0.5 text-inherit dark:bg-yellow-500/40"
          >
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

/** A line with its tokens coloured and the search term marked. */
function Colourised({
  text,
  term,
}: {
  text: string;
  term: string | null | undefined;
}) {
  return (
    <>
      {tokenize(text).map((t, i) =>
        t.kind ? (
          <span key={i} className={TOKEN_CLASS[t.kind]}>
            <Marked text={t.text} term={term} />
          </span>
        ) : (
          <Marked key={i} text={t.text} term={term} />
        ),
      )}
    </>
  );
}

export function LogsView({
  project,
  namespace,
  appId,
  trace = null,
  onClearTrace,
  fullPage = false,
  compact = false,
}: {
  project: string;
  /** Pins the view to one app's namespace (the app page). */
  namespace?: string;
  /** The app these logs belong to: its observability store is read when it has one. */
  appId?: number;
  /** Lines of one trace (the Logs tab's ?trace=). */
  trace?: string | null;
  onClearTrace?: () => void;
  fullPage?: boolean;
  /** The last lines only, live, without filters: the app overview card. */
  compact?: boolean;
}) {
  const [targets, setTargets] = useState<LogTargets | null>(null);
  const [filters, setFilters] = useState<LogFilters>({
    namespace: namespace ?? null,
  });
  const [searchInput, setSearchInput] = useState("");
  // A trace link may come from a trace hours old.
  const [period, setPeriod] = useState(trace ? "24h" : "1h");
  const [lines, setLines] = useState<Row[] | null>(null);
  const [live, setLive] = useState(compact);
  const [wrap, setWrap] = useState(true);
  const [showTime, setShowTime] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [context, setContext] = useState<{
    anchor: string;
    lines: Row[];
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const newest = useRef<string | null>(null);
  const top = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const t = useTranslations("activity");
  const locale = useLocale();
  const caps = useObsCapabilities(appId ?? 0, appId != null);
  const obs = appId != null && caps.data?.logs === true;
  // A failed capabilities request means the activity logs.
  const ready = appId == null || !caps.loading;
  // The activity logs have no trace field: the trace id is searched as text.
  const term = filters.search ?? trace;

  useEffect(() => {
    if (compact || obs || !ready) return;
    getLogTargets(project).then(setTargets, (e) => setError(e));
  }, [project, compact, obs, ready]);

  /** Newest first from either store: the selected period, or the live window. */
  const fetchRows = useCallback(
    async (follow: boolean): Promise<Row[]> => {
      const limit = follow ? 300 : compact ? COMPACT_LINES : obs ? OBS_LINES : 1000;
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
        q: filters.search ?? undefined,
        trace_id: trace ?? undefined,
        limit,
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
    [obs, appId, project, filters, term, trace, period, namespace, compact],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setContext(null);
    try {
      const result = await fetchRows(false);
      // The API answers newest first; a terminal reads oldest first.
      setLines([...result].reverse());
      newest.current = result[0]?.time ?? null;
      atBottom.current = true;
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [fetchRows]);

  useEffect(() => {
    if (ready) load();
  }, [load, ready]);

  useEffect(() => {
    if (!live || !ready) return;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const fresh = await fetchRows(true);
        if (fresh.length === 0) return;
        // The store re-reads a whole window: only unseen lines, none older
        // than the newest shown, so every line appears once and in order.
        const from = newest.current ? Date.parse(newest.current) : 0;
        setLines((current) => {
          const seen = new Set((current ?? []).map(key));
          const added = [...fresh]
            .reverse()
            .filter((l) => !seen.has(key(l)) && Date.parse(l.time) >= from);
          return [...(current ?? []), ...added].slice(
            -(compact ? COMPACT_LINES : MAX_LINES),
          );
        });
        if (Date.parse(fresh[0].time) >= from) newest.current = fresh[0].time;
        setError(null);
      } catch (e) {
        setError(e);
        // The compact card has no Follow button: it keeps retrying instead.
        if (!compact) setLive(false);
      }
    }, LIVE_MS);
    return () => clearInterval(timer);
  }, [live, ready, fetchRows, compact]);

  // Follow the end like a terminal, unless the reader scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && atBottom.current && !context) el.scrollTop = el.scrollHeight;
  }, [lines, context]);

  useEffect(() => {
    if (!fullscreen && !context) return;
    // Escape leaves the context view first, then fullscreen.
    const onKey = (e: KeyboardEvent) => {
      // A select or menu closing on Escape handles the key itself.
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (context) setContext(null);
      else setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen, context]);

  const showContext = async (anchor: LogLine) => {
    const at = new Date(anchor.time).getTime();
    try {
      const around = await getProjectLogs(project, {
        namespace: anchor.namespace,
        pod: anchor.pod,
        container: anchor.container,
        since: new Date(at - CONTEXT_MS).toISOString(),
        until: new Date(at + CONTEXT_MS).toISOString(),
        limit: 300,
      });
      setContext({ anchor: key(anchor), lines: [...around].reverse() });
    } catch (e) {
      setError(e);
    }
  };

  const set = (patch: Partial<LogFilters>) =>
    setFilters((f) => ({ ...f, ...patch }));
  const source = context ? context.lines : lines;

  const streams = useMemo(() => {
    const counts = new Map<string, number>();
    for (const l of lines ?? []) {
      const s = streamOf(l, project);
      counts.set(s, (counts.get(s) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [lines, project]);
  const width = Math.min(28, Math.max(8, ...streams.map(([s]) => s.length)));

  const shown = useMemo(
    () => (source ?? []).filter((l) => !hidden.has(streamOf(l, project))),
    [source, hidden, project],
  );

  const download = () => {
    const text = shown
      .map((l) => `${l.time} ${streamOf(l, project)} | ${l.line}`)
      .join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `logs-${project}.log`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const toggleStream = (s: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  const expanded = fullscreen || fullPage;
  const tab = new URLSearchParams();
  if (namespace) tab.set("namespace", namespace);
  // The new tab reads the same store as this one.
  if (appId != null) tab.set("app", String(appId));
  const tabQuery = tab.toString();

  return (
    <div
      ref={top}
      className={cn(
        "flex flex-col gap-4",
        fullscreen && "fixed inset-0 z-50 bg-background p-4",
        fullPage && !fullscreen && "h-[calc(100vh-8rem)]",
      )}
    >
      {!compact && (
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
                    .filter(
                      (p) => !filters.namespace || p.startsWith(filters.namespace),
                    )
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
            <NativeSelect
              aria-label={t("logs.aria.level")}
              value={filters.level ?? ""}
              onChange={(v) =>
                set({ level: (v || null) as LogFilters["level"] })
              }
            >
              <option value="">{t("logs.allLevels")}</option>
              <option value="error">{t("logs.errors")}</option>
              <option value="warn">{t("logs.warnings")}</option>
              {obs && (
                <>
                  <option value="info">{t("logs.info")}</option>
                  <option value="debug">{t("logs.debug")}</option>
                </>
              )}
            </NativeSelect>
            <NativeSelect
              aria-label={t("logs.aria.period")}
              value={period}
              onChange={setPeriod}
            >
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {t(`periods.${p.value}`)}
                </option>
              ))}
            </NativeSelect>
            <form
              // The store has no pod filters: the phrase takes their room.
              className={cn(
                "flex min-w-0 items-center gap-1",
                obs && "col-span-2 md:col-span-1 xl:col-span-4",
              )}
              onSubmit={(e) => {
                e.preventDefault();
                set({ search: searchInput.trim() || null });
              }}
            >
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                maxLength={MAX_SEARCH}
                placeholder={t("logs.search")}
                className="h-8 min-w-0 flex-1"
              />
              <Button
                type="submit"
                variant="ghost"
                size="sm"
                title={t("logs.searchButton")}
              >
                <Search className="h-4 w-4" />
              </Button>
            </form>
          </>
        }
        toggles={
          <>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={wrap}
                onChange={(e) => setWrap(e.target.checked)}
              />
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
            <span className="text-muted-foreground">
              {t("logs.lineCount", { count: shown.length })}
            </span>
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
            <Button
              variant="ghost"
              size="sm"
              onClick={download}
              title={t("logs.download")}
            >
              <Download className="h-4 w-4" />
            </Button>
            {!fullPage && (
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
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setFullscreen((v) => !v)}
              title={
                fullscreen ? t("logs.exitFullscreen") : t("logs.fullscreen")
              }
            >
              {fullscreen ? (
                <Minimize2 className="h-4 w-4" />
              ) : (
                <Maximize2 className="h-4 w-4" />
              )}
            </Button>
            <Button
              variant={live ? "default" : "outline"}
              size="sm"
              onClick={() => setLive((v) => !v)}
            >
              {live ? (
                <Pause className="mr-1.5 h-4 w-4" />
              ) : (
                <Play className="mr-1.5 h-4 w-4" />
              )}
              {t(live ? "logs.live" : "logs.follow")}
            </Button>
          </>
        }
      />
      )}

      {!compact && streams.length > 1 && (
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

      {error != null &&
        (isNotConnected(error) ? (
          <NotConnected cloud={isNotConnected(error)!.cloud} />
        ) : (
          <p className="flex items-center gap-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" /> {errorMessage(error)}
          </p>
        ))}

      {trace && (
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
          <span>{t("logs.contextBanner")}</span>
          <Button variant="outline" size="sm" onClick={() => setContext(null)}>
            <ArrowLeft className="mr-1.5 h-4 w-4" />
            {t("logs.backToResults")}
            <kbd className="ml-2 rounded border px-1 font-mono text-[10px] text-muted-foreground">
              Esc
            </kbd>
          </Button>
        </div>
      )}

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottom.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className={cn(
          "overflow-auto rounded-xl border bg-zinc-50 py-1 font-mono text-xs leading-5 dark:bg-zinc-950",
          expanded ? "min-h-0 flex-1" : compact ? "h-64" : "h-[36rem]",
        )}
      >
        {source === null ? (
          <p className="p-4 text-muted-foreground">{t("common.loading")}</p>
        ) : shown.length === 0 ? (
          <p className="p-4 text-muted-foreground">{t("logs.noLines")}</p>
        ) : (
          shown.map((l, i) => {
            const s = streamOf(l, project);
            // A new block starts when the process changes: a hairline and a
            // little air, and the name only at the top of the block.
            const first = i === 0 || streamOf(shown[i - 1], project) !== s;
            const level = severity(l);
            return (
              <div
                key={key(l)}
                className={cn(
                  "group flex gap-3 border-l-2 border-transparent px-3 hover:bg-muted/60",
                  first && i > 0 && "mt-1 border-t border-t-border/70 pt-1",
                  level === "error" && "border-l-red-500 bg-red-500/5",
                  level === "warn" && "border-l-amber-500",
                  context?.anchor === key(l) && "bg-primary/10",
                )}
              >
                {showTime && (
                  <span className="shrink-0 text-muted-foreground">
                    {clock(l.time, locale)}
                  </span>
                )}
                <span
                  className={cn(
                    "shrink-0 truncate text-right font-semibold",
                    colourOf(s),
                    !first && "opacity-35",
                  )}
                  style={{ width: `${width}ch` }}
                  title={`${l.pod} / ${l.container}`}
                >
                  {s}
                </span>
                <span className="shrink-0 text-muted-foreground">|</span>
                <span
                  className={cn(
                    "min-w-0 flex-1",
                    wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre",
                  )}
                >
                  <Colourised text={l.line} term={term} />
                </span>
                {l.trace_id && appId != null && (
                  <Link
                    href={`/projects/${encodeURIComponent(project)}/apps/${appId}/traces?trace=${encodeURIComponent(l.trace_id)}`}
                    title={t("logs.openTrace")}
                    aria-label={t("logs.openTrace")}
                    className="flex h-5 shrink-0 items-center self-start rounded px-1 text-primary hover:bg-muted"
                  >
                    <Waypoints className="h-3.5 w-3.5" aria-hidden />
                  </Link>
                )}
                {!context && !compact && !obs && (
                  // An explicit button: a click anywhere on the line used to
                  // switch views, which selecting text triggered by mistake.
                  <button
                    type="button"
                    onClick={() => showContext(l)}
                    title={t("logs.showContext")}
                    aria-label={t("logs.showContext")}
                    tabIndex={-1}
                    className="shrink-0 self-start rounded px-1 text-muted-foreground opacity-0 hover:bg-muted hover:text-foreground group-hover:opacity-100 [@media(hover:none)]:opacity-60"
                  >
                    <ListTree className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>

      {!compact && !fullscreen && (
        <>
          {obs && appId != null && (
            <TopErrors
              appId={appId}
              base={`/projects/${encodeURIComponent(project)}/apps/${appId}`}
              range={period}
              onPick={(row) => {
                const words = row.pattern.split("<N>")[0].trim().slice(0, MAX_SEARCH);
                setSearchInput(words);
                set({ level: "error", search: words || null });
                top.current?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            />
          )}
          <VolumeCard project={project} namespace={namespace} />
          <NetworkPanel project={project} />
        </>
      )}
    </div>
  );
}
