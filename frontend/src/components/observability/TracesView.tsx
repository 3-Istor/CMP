"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getObsTrace, listObsTraces } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { TraceDetail, TraceSpan } from "@/types/observability";
import { ArrowLeft, ScrollText } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useId, useMemo, useState } from "react";
import { CardState, RangeSelect, Section, useObsFormat } from "./shared";

const RANGES = ["15m", "1h", "6h", "24h"] as const;

/** Traces of the app; `?trace=<id>` opens one as a waterfall. */
export function TracesView({ appId, base }: { appId: number; base: string }) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const traceId = params.get("trace");
  const open = (id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("trace", id);
    else next.delete("trace");
    const text = next.toString();
    router.push(text ? `${pathname}?${text}` : pathname);
  };
  return traceId ? (
    <TraceWaterfall appId={appId} base={base} traceId={traceId} onBack={() => open(null)} />
  ) : (
    <TraceList appId={appId} onOpen={open} />
  );
}

function TraceList({ appId, onOpen }: { appId: number; onOpen: (id: string) => void }) {
  const t = useTranslations("obs.traces");
  const { time, ms } = useObsFormat();
  const ids = { errors: useId(), min: useId() };
  const [range, setRange] = useState("1h");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [minDuration, setMinDuration] = useState("");
  const min = Number(minDuration) > 0 ? Math.floor(Number(minDuration)) : undefined;
  const card = useCardData(
    `obs-traces-${appId}-${range}-${errorsOnly}-${min ?? ""}`,
    () => listObsTraces(appId, { range, errors_only: errorsOnly, min_duration_ms: min, limit: 50 }),
  );
  return (
    <Section
      title={t("title")}
      description={t("description")}
      actions={
        <div className="flex flex-wrap items-center gap-3">
          <RangeSelect value={range} options={RANGES} onChange={setRange} />
          <label htmlFor={ids.errors} className="flex items-center gap-2 text-sm">
            <input
              id={ids.errors}
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={errorsOnly}
              onChange={(e) => setErrorsOnly(e.target.checked)}
            />
            {t("errorsOnly")}
          </label>
          <div className="flex items-center gap-2">
            <label htmlFor={ids.min} className="text-xs text-muted-foreground">
              {t("minDuration")}
            </label>
            <Input
              id={ids.min}
              type="number"
              min={1}
              inputMode="numeric"
              className="h-8 w-24"
              value={minDuration}
              onChange={(e) => setMinDuration(e.target.value)}
            />
          </div>
        </div>
      }
    >
      <CardState card={card} signal="traces">
        {(rows) =>
          rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">{t("operation")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("start")}</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">{t("duration")}</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">{t("spans")}</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">{t("errors")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rows.map((r) => (
                    <tr key={r.trace_id} className="hover:bg-muted/30">
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        <button
                          type="button"
                          onClick={() => onOpen(r.trace_id)}
                          className="text-left font-medium text-primary hover:underline focus-visible:underline"
                          aria-label={t("open", { id: r.trace_id.slice(0, 8) })}
                        >
                          {r.root_operation || r.trace_id.slice(0, 8)}
                        </button>
                        <span className="ml-2 font-mono text-xs text-muted-foreground">
                          {r.root_service}
                        </span>
                      </th>
                      <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">{time(r.start)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{ms(r.duration_ms)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{r.spans}</td>
                      <td className="px-3 py-2 text-right">
                        {r.errors > 0 ? (
                          <Badge variant="outline" className="border-red-600/40 text-red-700 dark:text-red-300">
                            {r.errors}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">0</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </CardState>
    </Section>
  );
}

function depths(spans: TraceSpan[]): Map<string, number> {
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const found = new Map<string, number>();
  const depth = (s: TraceSpan, seen = 0): number => {
    if (found.has(s.span_id)) return found.get(s.span_id)!;
    const parent = s.parent_id ? byId.get(s.parent_id) : undefined;
    const value = parent && seen < 64 ? depth(parent, seen + 1) + 1 : 0;
    found.set(s.span_id, value);
    return value;
  };
  spans.forEach((s) => depth(s));
  return found;
}

/** Parents before their children, siblings by start time. */
function ordered(spans: TraceSpan[]): TraceSpan[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string | null, TraceSpan[]>();
  for (const s of spans) {
    const parent = s.parent_id && ids.has(s.parent_id) ? s.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), s]);
  }
  const out: TraceSpan[] = [];
  const walk = (parent: string | null) => {
    for (const s of (children.get(parent) ?? []).sort((a, b) => a.start_us - b.start_us)) {
      out.push(s);
      walk(s.span_id);
    }
  };
  walk(null);
  return out;
}

function TraceWaterfall({
  appId,
  base,
  traceId,
  onBack,
}: {
  appId: number;
  base: string;
  traceId: string;
  onBack: () => void;
}) {
  const t = useTranslations("obs.traces");
  const { time, ms } = useObsFormat();
  const card = useCardData(`obs-trace-${appId}-${traceId}`, () => getObsTrace(appId, traceId));
  const notFound = card.error?.includes("Unknown trace");
  return (
    <Section
      title={t("waterfall")}
      description={traceId}
      actions={
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={onBack}>
            <ArrowLeft aria-hidden />
            {t("back")}
          </Button>
          <Link
            href={`${base}/logs?trace=${encodeURIComponent(traceId)}`}
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            <ScrollText className="h-4 w-4" aria-hidden />
            {t("logs")}
          </Link>
        </div>
      }
    >
      {notFound ? (
        <p role="status" className="text-sm text-muted-foreground">{t("notFound")}</p>
      ) : (
        <CardState card={card} signal="traces">
          {(trace) => <Waterfall trace={trace} time={time} ms={ms} />}
        </CardState>
      )}
    </Section>
  );
}

function Waterfall({
  trace,
  time,
  ms,
}: {
  trace: TraceDetail;
  time: (iso: string) => string;
  ms: (value: number) => string;
}) {
  const t = useTranslations("obs.traces");
  const spans = useMemo(() => ordered(trace.span_list), [trace]);
  const depth = useMemo(() => depths(trace.span_list), [trace]);
  const start = Math.min(...trace.span_list.map((s) => s.start_us));
  const total = Math.max(1, trace.duration_ms * 1000);
  const [selected, setSelected] = useState<string | null>(null);
  const picked = spans.find((s) => s.span_id === selected);
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {trace.root_service} · {time(trace.start)} · {ms(trace.duration_ms)} · {t("spans")} {trace.spans}
      </p>
      {trace.truncated && <p className="text-xs text-muted-foreground">{t("truncated")}</p>}
      <ul className="space-y-1" aria-label={t("waterfall")}>
        {spans.map((s) => {
          const left = ((s.start_us - start) / total) * 100;
          const width = Math.max(0.5, (s.duration_us / total) * 100);
          const label = t("spanLabel", {
            service: s.service,
            operation: s.operation,
            offset: ms((s.start_us - start) / 1000),
            duration: ms(s.duration_us / 1000),
          });
          return (
            <li key={s.span_id}>
              <button
                type="button"
                onClick={() => setSelected(selected === s.span_id ? null : s.span_id)}
                aria-expanded={selected === s.span_id}
                aria-label={label}
                className="grid w-full grid-cols-[minmax(0,2fr)_minmax(0,3fr)_4.5rem] items-center gap-3 rounded-md px-1 py-0.5 text-left text-xs hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"
              >
                <span className="truncate" style={{ paddingLeft: `${(depth.get(s.span_id) ?? 0) * 14}px` }}>
                  <span className={cn("font-medium", s.error && "text-destructive")}>{s.operation}</span>{" "}
                  <span className="text-muted-foreground">{s.service}</span>
                </span>
                <span className="relative h-4 rounded bg-muted/40" aria-hidden>
                  <span
                    className={cn(
                      "absolute top-0 h-4 rounded",
                      s.error ? "bg-destructive/80" : "bg-[var(--chart-1)]",
                    )}
                    style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
                  />
                </span>
                <span className="text-right tabular-nums text-muted-foreground" aria-hidden>
                  {ms(s.duration_us / 1000)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {picked && (
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-xl border p-3 text-xs">
          <dt className="text-muted-foreground">{t("service")}</dt>
          <dd className="font-mono">{picked.service}</dd>
          {Object.entries(picked.tags).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-all font-mono">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
