"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { downloadActivity, getActivity, getActivitySummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  ActivityEvent,
  ActivityFilters,
  ActivityKpi,
  ActivitySource,
  ActivitySummary,
} from "@/types";
import { AlertTriangle, Download, Loader2, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ACTION_GROUPS,
  describe,
  errorMessage,
  SOURCE_META,
  SOURCE_ORDER,
  timeLabel,
} from "./labels";
import { NativeSelect } from "./NativeSelect";

const TOOLTIP_STYLE = {
  borderRadius: 8,
  fontSize: 12,
  background: "var(--popover)",
  border: "1px solid var(--border)",
};

function delta(kpi: ActivityKpi): string {
  if (kpi.previous === 0) return kpi.value === 0 ? "" : "nouveau";
  const pct = Math.round(((kpi.value - kpi.previous) / kpi.previous) * 100);
  return `${pct >= 0 ? "+" : ""}${pct} % vs période -1`;
}

function KpiTile({
  label,
  kpi,
  warn,
  onClick,
}: {
  label: string;
  kpi: ActivityKpi;
  warn?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "rounded-xl border p-4 text-left transition-colors",
        warn && kpi.value > 0
          ? "border-amber-500/50 bg-amber-500/10"
          : "border-border",
        onClick && "hover:bg-muted/50",
      )}
    >
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-semibold">{kpi.value}</span>
        <span className="text-xs text-muted-foreground">{delta(kpi)}</span>
      </p>
    </button>
  );
}

function ActivityChart({ summary }: { summary: ActivitySummary }) {
  const data = summary.days.map((d) => ({
    day: new Date(`${d.day}T00:00:00Z`).toLocaleDateString("fr-FR", {
      day: "numeric",
      month: "short",
    }),
    ...d.counts,
  }));
  const used = SOURCE_ORDER.filter(
    (s) => s !== "keycloak" && summary.days.some((d) => d.counts[s]),
  );
  if (used.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        Aucune activité sur la période.
      </p>
    );
  }
  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
          <CartesianGrid vertical={false} strokeOpacity={0.15} />
          <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
          {used.map((s) => (
            <Bar
              key={s}
              dataKey={s}
              name={SOURCE_META[s].label}
              stackId="a"
              fill={SOURCE_META[s].color}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function EventRow({
  event,
  selected,
  onSelect,
}: {
  event: ActivityEvent;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
        selected ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50",
      )}
    >
      <span
        className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
        style={{ background: SOURCE_META[event.source].color }}
      />
      <span className="min-w-0 flex-1">
        <span className="block text-sm">
          <span className="font-medium">{event.actor}</span> {describe(event)}
        </span>
        <span className="block text-xs text-muted-foreground">
          {SOURCE_META[event.source].label} · {timeLabel(event.time)}
          {event.outcome === "failure" && (
            <span className="text-red-600 dark:text-red-400"> · refusé</span>
          )}
        </span>
      </span>
      {event.notable && (
        <Badge
          variant="outline"
          className="shrink-0 border-amber-500/40 bg-amber-500/15 text-amber-700 dark:text-amber-300"
        >
          Notable
        </Badge>
      )}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

function EventDetail({
  event,
  onClose,
  onActor,
}: {
  event: ActivityEvent;
  onClose: () => void;
  onActor: (actor: string) => void;
}) {
  const [raw, setRaw] = useState(false);
  const d = event.details;
  return (
    <Card className="lg:sticky lg:top-4">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">Détail de l&apos;événement</CardTitle>
        <Button variant="ghost" size="icon" onClick={onClose} title="Fermer">
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        <Field label="Qui">{event.actor}</Field>
        <Field label="Quoi">{describe(event)}</Field>
        <Field label="Source">{SOURCE_META[event.source].label}</Field>
        <Field label="Quand">{new Date(event.time).toLocaleString("fr-FR")}</Field>
        {event.app && <Field label="App">{event.app}</Field>}
        {event.target && <Field label="Cible">{event.target}</Field>}
        {event.source_ip && <Field label="Depuis">{event.source_ip}</Field>}
        <Field label="Résultat">
          {event.outcome === "success" ? "réussi" : "refusé"}
          {event.status_code ? ` (${event.status_code})` : ""}
        </Field>
        {typeof d.request_uri === "string" && (
          <Field label="Requête">
            <code className="text-xs">{decodeURIComponent(d.request_uri)}</code>
          </Field>
        )}
        <div className="flex flex-col gap-1 border-t pt-3 text-sm">
          <button
            type="button"
            className="text-left text-primary hover:underline"
            onClick={() => onActor(event.actor)}
          >
            Toute l&apos;activité de {event.actor}
          </button>
          <button
            type="button"
            className="text-left text-primary hover:underline"
            onClick={() => setRaw((v) => !v)}
          >
            {raw ? "Masquer" : "Voir"} les données brutes
          </button>
        </div>
        {raw && (
          <pre className="max-h-72 overflow-auto rounded-md bg-muted p-2 text-xs">
            {JSON.stringify(event, null, 2)}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}

export function AuditView({
  project,
  canExport,
  showLogins,
}: {
  project: string;
  canExport: boolean;
  showLogins: boolean;
}) {
  const [filters, setFilters] = useState<ActivityFilters>({ days: 7 });
  const [summary, setSummary] = useState<ActivitySummary | null>(null);
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [unavailable, setUnavailable] = useState<ActivitySource[]>([]);
  const [selected, setSelected] = useState<ActivityEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [more, setMore] = useState(true);

  const set = (patch: Partial<ActivityFilters>) =>
    setFilters((f) => ({ ...f, ...patch, until: null }));

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, feed] = await Promise.all([
        getActivitySummary(project, Math.min(filters.days ?? 7, 30)),
        getActivity(project, filters),
      ]);
      setSummary(s);
      setEvents(feed.events);
      setUnavailable([...new Set([...s.unavailable, ...feed.unavailable])]);
      setMore(feed.events.length >= 100);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [project, filters]);

  useEffect(() => {
    load();
  }, [load]);

  const loadMore = async () => {
    if (!events?.length) return;
    setLoading(true);
    try {
      const last = events[events.length - 1];
      const feed = await getActivity(project, { ...filters, until: last.time });
      const seen = new Set(events.map((e) => e.id));
      setEvents([...events, ...feed.events.filter((e) => !seen.has(e.id))]);
      setMore(feed.events.length >= 100);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const actors = useMemo(() => {
    const names = new Set<string>(summary?.top_actors.map(([a]) => a) ?? []);
    events?.forEach((e) => names.add(e.actor));
    if (filters.actor) names.add(filters.actor);
    return [...names].sort();
  }, [summary, events, filters.actor]);

  const apps = useMemo(
    () => [...new Set(events?.map((e) => e.app).filter(Boolean) as string[])].sort(),
    [events],
  );

  if (error && !events) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
        <AlertTriangle className="h-4 w-4" /> {error}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {summary ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KpiTile label={`Actions · ${filters.days ?? 7} j`} kpi={summary.actions} />
          <KpiTile label="Personnes actives" kpi={summary.people} />
          <KpiTile
            label="Événements notables"
            kpi={summary.notable}
            warn
            onClick={() => set({ notable_only: true })}
          />
          {showLogins && summary.login_failures && (
            <KpiTile
              label="Échecs de connexion"
              kpi={summary.login_failures}
              warn
              onClick={() => set({ action: "keycloak.login_error", source: null })}
            />
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Activité par jour et par source</CardTitle>
        </CardHeader>
        <CardContent>
          {summary ? <ActivityChart summary={summary} /> : <Skeleton className="h-48" />}
        </CardContent>
      </Card>

      {unavailable.length > 0 && (
        <p className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4" />
          Sources momentanément indisponibles :{" "}
          {unavailable.map((s) => SOURCE_META[s].label).join(", ")}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 p-2">
        <NativeSelect
          aria-label="Source"
          value={filters.source ?? ""}
          onChange={(v) => set({ source: (v || null) as ActivitySource | null })}
        >
          <option value="">Toutes les sources</option>
          {SOURCE_ORDER.filter((s) => showLogins || s !== "keycloak").map((s) => (
            <option key={s} value={s}>
              {SOURCE_META[s].label}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Personne"
          value={filters.actor ?? ""}
          onChange={(v) => set({ actor: v || null })}
        >
          <option value="">Toutes les personnes</option>
          {actors.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Action"
          value={filters.action ?? ""}
          onChange={(v) => set({ action: v || null })}
        >
          <option value="">Toutes les actions</option>
          {ACTION_GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="App"
          value={filters.app ?? ""}
          onChange={(v) => set({ app: v || null })}
        >
          <option value="">Toutes les apps</option>
          {apps.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Période"
          value={String(filters.days ?? 7)}
          onChange={(v) => set({ days: Number(v) })}
        >
          <option value="1">24 heures</option>
          <option value="7">7 jours</option>
          <option value="14">14 jours</option>
          <option value="30">30 jours</option>
        </NativeSelect>
        <label className="flex items-center gap-1.5 px-1 text-sm">
          <input
            type="checkbox"
            checked={!!filters.include_reads}
            onChange={(e) => set({ include_reads: e.target.checked })}
          />
          Lectures
        </label>
        <label className="flex items-center gap-1.5 px-1 text-sm">
          <input
            type="checkbox"
            checked={!!filters.notable_only}
            onChange={(e) => set({ notable_only: e.target.checked })}
          />
          Notables seulement
        </label>
        <div className="ml-auto flex gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading} title="Rafraîchir">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
          {canExport && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  downloadActivity(project, filters, "csv").catch((e) =>
                    setError(errorMessage(e)),
                  )
                }
              >
                <Download className="mr-1.5 h-4 w-4" /> CSV
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  downloadActivity(project, filters, "json").catch((e) =>
                    setError(errorMessage(e)),
                  )
                }
              >
                <Download className="mr-1.5 h-4 w-4" /> JSON
              </Button>
            </>
          )}
        </div>
      </div>

      <div className={cn("grid gap-4", selected && "lg:grid-cols-[1fr_22rem]")}>
        <div className="space-y-2">
          {events === null ? (
            [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)
          ) : events.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Aucun événement pour ces filtres.
            </p>
          ) : (
            events.map((e) => (
              <EventRow
                key={e.id}
                event={e}
                selected={selected?.id === e.id}
                onSelect={() => setSelected(e)}
              />
            ))
          )}
          {events && events.length > 0 && more && (
            <Button variant="outline" className="w-full" onClick={loadMore} disabled={loading}>
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Charger plus
            </Button>
          )}
        </div>
        {selected && (
          <EventDetail
            event={selected}
            onClose={() => setSelected(null)}
            onActor={(actor) => {
              setSelected(null);
              set({ actor });
            }}
          />
        )}
      </div>
    </div>
  );
}
