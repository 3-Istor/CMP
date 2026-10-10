"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getLogTargets, getProjectLogs } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { LogFilters, LogLine, LogTargets } from "@/types";
import {
  AlertTriangle,
  Download,
  ExternalLink,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RefreshCw,
  Search,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FilterBar } from "./FilterBar";
import { errorMessage } from "./labels";
import { NativeSelect } from "./NativeSelect";

const LIVE_MS = 3000;
const CONTEXT_MS = 60_000;
const MAX_LINES = 3000;

const PERIODS: { value: string; label: string; ms: number }[] = [
  { value: "15m", label: "15 minutes", ms: 15 * 60_000 },
  { value: "1h", label: "1 heure", ms: 3_600_000 },
  { value: "6h", label: "6 heures", ms: 6 * 3_600_000 },
  { value: "24h", label: "24 heures", ms: 24 * 3_600_000 },
];

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

function levelClass(line: string): string {
  if (/\b(error|err|fatal|panic|exception)\b/i.test(line))
    return "text-red-700 dark:text-red-400";
  if (/\b(warn|warning)\b/i.test(line))
    return "text-amber-700 dark:text-amber-300";
  return "";
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function key(l: LogLine): string {
  return `${l.time}|${l.pod}|${l.container}|${l.line}`;
}

function Highlighted({
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

export function LogsView({
  project,
  fullPage = false,
}: {
  project: string;
  fullPage?: boolean;
}) {
  const [targets, setTargets] = useState<LogTargets | null>(null);
  const [filters, setFilters] = useState<LogFilters>({});
  const [searchInput, setSearchInput] = useState("");
  const [period, setPeriod] = useState("1h");
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [live, setLive] = useState(false);
  const [wrap, setWrap] = useState(true);
  const [showTime, setShowTime] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [context, setContext] = useState<{
    anchor: string;
    lines: LogLine[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const newest = useRef<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  useEffect(() => {
    getLogTargets(project).then(setTargets, (e) => setError(errorMessage(e)));
  }, [project]);

  const load = useCallback(async () => {
    setLoading(true);
    setContext(null);
    try {
      const ms = PERIODS.find((p) => p.value === period)?.ms ?? 3_600_000;
      const result = await getProjectLogs(project, {
        ...filters,
        since: new Date(Date.now() - ms).toISOString(),
        limit: 1000,
      });
      // The API answers newest first; a terminal reads oldest first.
      setLines([...result].reverse());
      newest.current = result[0]?.time ?? null;
      atBottom.current = true;
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [project, filters, period]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(async () => {
      try {
        const since =
          newest.current ?? new Date(Date.now() - LIVE_MS).toISOString();
        const fresh = await getProjectLogs(project, {
          ...filters,
          since,
          limit: 300,
        });
        if (fresh.length === 0) return;
        setLines((current) => {
          const seen = new Set((current ?? []).map(key));
          const added = [...fresh].reverse().filter((l) => !seen.has(key(l)));
          return [...(current ?? []), ...added].slice(-MAX_LINES);
        });
        newest.current = fresh[0].time;
      } catch (e) {
        setError(errorMessage(e));
        setLive(false);
      }
    }, LIVE_MS);
    return () => clearInterval(timer);
  }, [live, project, filters]);

  // Follow the end like a terminal, unless the reader scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && atBottom.current && !context) el.scrollTop = el.scrollHeight;
  }, [lines, context]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) =>
      e.key === "Escape" && setFullscreen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

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
      setError(errorMessage(e));
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

  return (
    <div
      className={cn(
        "flex flex-col gap-4",
        fullscreen && "fixed inset-0 z-50 bg-background p-4",
        fullPage && !fullscreen && "h-[calc(100vh-8rem)]",
      )}
    >
      <FilterBar
        selects={
          <>
            <NativeSelect
              aria-label="Namespace"
              value={filters.namespace ?? ""}
              onChange={(v) => set({ namespace: v || null, pod: null })}
            >
              <option value="">Tous les namespaces</option>
              {targets?.namespaces.map((n) => (
                <option key={n} value={n}>
                  {n.replace(`${project}-`, "")}
                </option>
              ))}
            </NativeSelect>
            <NativeSelect
              aria-label="Pod"
              value={filters.pod ?? ""}
              onChange={(v) => set({ pod: v || null })}
            >
              <option value="">Tous les pods</option>
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
              aria-label="Conteneur"
              value={filters.container ?? ""}
              onChange={(v) => set({ container: v || null })}
            >
              <option value="">Tous les conteneurs</option>
              {targets?.containers.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </NativeSelect>
            <NativeSelect
              aria-label="Niveau"
              value={filters.level ?? ""}
              onChange={(v) =>
                set({ level: (v || null) as LogFilters["level"] })
              }
            >
              <option value="">Tous les niveaux</option>
              <option value="error">Erreurs</option>
              <option value="warn">Avertissements</option>
            </NativeSelect>
            <NativeSelect
              aria-label="Période"
              value={period}
              onChange={setPeriod}
            >
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </NativeSelect>
            <form
              className="flex min-w-0 items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                set({ search: searchInput.trim() || null });
              }}
            >
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Rechercher…"
                className="h-8 min-w-0 flex-1"
              />
              <Button
                type="submit"
                variant="ghost"
                size="sm"
                title="Rechercher"
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
              Retour à la ligne
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showTime}
                onChange={(e) => setShowTime(e.target.checked)}
              />
              Horodatage
            </label>
            <span className="text-muted-foreground">
              {shown.length} ligne{shown.length > 1 ? "s" : ""}
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
              title="Rafraîchir"
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={download}
              title="Télécharger les lignes affichées"
            >
              <Download className="h-4 w-4" />
            </Button>
            {!fullPage && (
              <Link
                href={`/projects/${project}/logs`}
                target="_blank"
                rel="noopener noreferrer"
                title="Ouvrir dans un nouvel onglet"
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
                fullscreen ? "Quitter le plein écran (Échap)" : "Plein écran"
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
              {live ? "En direct" : "Suivre"}
            </Button>
          </>
        }
      />

      {streams.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {streams.map(([s, count]) => (
            <button
              key={s}
              type="button"
              onClick={() => toggleStream(s)}
              title={hidden.has(s) ? "Afficher ce flux" : "Masquer ce flux"}
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

      {error && (
        <p className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </p>
      )}

      {context && (
        <div className="flex items-center justify-between rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          <span>
            Contexte : une minute avant et après la ligne choisie, même
            conteneur.
          </span>
          <Button variant="ghost" size="sm" onClick={() => setContext(null)}>
            Retour aux résultats
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
          expanded ? "min-h-0 flex-1" : "h-[36rem]",
        )}
      >
        {source === null ? (
          <p className="p-4 text-muted-foreground">Chargement…</p>
        ) : shown.length === 0 ? (
          <p className="p-4 text-muted-foreground">
            Aucune ligne pour ces filtres.
          </p>
        ) : (
          shown.map((l) => {
            const s = streamOf(l, project);
            return (
              <div
                key={key(l)}
                onClick={() => !context && showContext(l)}
                title={context ? undefined : "Voir le contexte"}
                className={cn(
                  "flex cursor-pointer gap-3 px-3 hover:bg-muted/60",
                  context?.anchor === key(l) && "bg-primary/10",
                )}
              >
                {showTime && (
                  <span className="shrink-0 text-muted-foreground">
                    {clock(l.time)}
                  </span>
                )}
                <span
                  className={cn(
                    "shrink-0 truncate text-right font-semibold",
                    colourOf(s),
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
                    levelClass(l.line),
                  )}
                >
                  <Highlighted text={l.line} term={filters.search} />
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
