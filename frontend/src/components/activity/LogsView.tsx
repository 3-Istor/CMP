"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getLogTargets, getProjectLogs } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { LogFilters, LogLine, LogTargets } from "@/types";
import { AlertTriangle, Pause, Play, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./labels";
import { NativeSelect } from "./NativeSelect";

const LIVE_MS = 3000;
const CONTEXT_MS = 60_000;

const PERIODS: { value: string; label: string; ms: number }[] = [
  { value: "15m", label: "15 minutes", ms: 15 * 60_000 },
  { value: "1h", label: "1 heure", ms: 3_600_000 },
  { value: "6h", label: "6 heures", ms: 6 * 3_600_000 },
  { value: "24h", label: "24 heures", ms: 24 * 3_600_000 },
];

function levelClass(line: string): string {
  if (/\b(error|err|fatal|panic|exception)\b/i.test(line))
    return "text-red-700 dark:text-red-400";
  if (/\b(warn|warning)\b/i.test(line)) return "text-amber-700 dark:text-amber-300";
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

export function LogsView({ project }: { project: string }) {
  const [targets, setTargets] = useState<LogTargets | null>(null);
  const [filters, setFilters] = useState<LogFilters>({});
  const [searchInput, setSearchInput] = useState("");
  const [period, setPeriod] = useState("1h");
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [live, setLive] = useState(false);
  const [context, setContext] = useState<{ anchor: string; lines: LogLine[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const newest = useRef<string | null>(null);

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
      });
      setLines(result);
      newest.current = result[0]?.time ?? null;
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
        const since = newest.current ?? new Date(Date.now() - LIVE_MS).toISOString();
        const fresh = await getProjectLogs(project, { ...filters, since, limit: 200 });
        if (fresh.length === 0) return;
        setLines((current) => {
          const seen = new Set((current ?? []).map(key));
          const added = fresh.filter((l) => !seen.has(key(l)));
          return [...added, ...(current ?? [])].slice(0, 2000);
        });
        newest.current = fresh[0].time;
      } catch (e) {
        setError(errorMessage(e));
        setLive(false);
      }
    }, LIVE_MS);
    return () => clearInterval(timer);
  }, [live, project, filters]);

  const showContext = async (anchor: LogLine) => {
    const at = new Date(anchor.time).getTime();
    try {
      const around = await getProjectLogs(project, {
        namespace: anchor.namespace,
        pod: anchor.pod,
        container: anchor.container,
        since: new Date(at - CONTEXT_MS).toISOString(),
        until: new Date(at + CONTEXT_MS).toISOString(),
        limit: 200,
      });
      setContext({ anchor: key(anchor), lines: [...around].reverse() });
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const set = (patch: Partial<LogFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const shown = context ? context.lines : lines;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 p-2">
        <NativeSelect
          aria-label="App"
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
            .filter((p) => !filters.namespace || p.startsWith(filters.namespace))
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
          onChange={(v) => set({ level: (v || null) as LogFilters["level"] })}
        >
          <option value="">Tous les niveaux</option>
          <option value="error">Erreurs</option>
          <option value="warn">Avertissements</option>
        </NativeSelect>
        <NativeSelect aria-label="Période" value={period} onChange={setPeriod}>
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </NativeSelect>
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            set({ search: searchInput.trim() || null });
          }}
        >
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Rechercher…"
            className="h-8 w-48"
          />
          <Button type="submit" variant="ghost" size="sm" title="Rechercher">
            <Search className="h-4 w-4" />
          </Button>
        </form>
        <div className="ml-auto flex gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading} title="Rafraîchir">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
          <Button
            variant={live ? "default" : "outline"}
            size="sm"
            onClick={() => setLive((v) => !v)}
          >
            {live ? <Pause className="mr-1.5 h-4 w-4" /> : <Play className="mr-1.5 h-4 w-4" />}
            {live ? "En direct" : "Suivre"}
          </Button>
        </div>
      </div>

      {error && (
        <p className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {error}
        </p>
      )}

      {context && (
        <div className="flex items-center justify-between rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          <span>Contexte : une minute avant et après la ligne choisie, même conteneur.</span>
          <Button variant="ghost" size="sm" onClick={() => setContext(null)}>
            Retour aux résultats
          </Button>
        </div>
      )}

      <div className="max-h-[36rem] overflow-auto rounded-xl border bg-muted/20 font-mono text-xs">
        {shown === null ? (
          <p className="p-4 text-muted-foreground">Chargement…</p>
        ) : shown.length === 0 ? (
          <p className="p-4 text-muted-foreground">Aucune ligne pour ces filtres.</p>
        ) : (
          shown.map((l) => (
            <button
              type="button"
              key={key(l)}
              onClick={() => !context && showContext(l)}
              title={context ? undefined : "Voir le contexte"}
              className={cn(
                "flex w-full gap-3 border-b border-border/40 px-3 py-1 text-left hover:bg-muted/60",
                context?.anchor === key(l) && "bg-primary/10",
              )}
            >
              <span className="shrink-0 text-muted-foreground">{clock(l.time)}</span>
              <span className="w-40 shrink-0 truncate text-muted-foreground" title={l.pod}>
                {l.pod}
              </span>
              <span className={cn("min-w-0 flex-1 whitespace-pre-wrap break-all", levelClass(l.line))}>
                {l.line}
              </span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
