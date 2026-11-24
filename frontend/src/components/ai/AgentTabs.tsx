"use client";

import { Badge } from "@/components/ui/badge";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Skeleton } from "@/components/ui/skeleton";
import { getActivity, getAgentTraces, getAgentUsage } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { useFormat } from "@/lib/useFormat";
import { cn } from "@/lib/utils";
import type { AgentTrace, RefusalReason } from "@/types/ai";
import { AlertCircle, ExternalLink, ShieldCheck, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import { useAgentContext } from "./AgentFrame";
import { CapabilityMap, type CapabilityNode } from "./CapabilityMap";
import { RiskBadge, RingGauge, Sparkline, useAiFormat } from "./shared";

const panel = "rounded-xl bg-card p-4 ring-1 ring-foreground/10";
const REASONS: RefusalReason[] = ["budget", "policy", "ratelimit", "guardrail"];

function LoadError({ message }: { message: string }) {
  return (
    <p className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
      <AlertCircle className="size-4" aria-hidden />
      {message}
    </p>
  );
}

export function AgentOverview() {
  const t = useTranslations("ai.overview");
  const tReason = useTranslations("ai.refusal");
  const f = useAiFormat();
  const { project, agent } = useAgentContext();
  const today = useCardData(`agent-usage-1d-${project}-${agent.name}`, () => getAgentUsage(project, agent.name, "1d"), {
    intervalMs: 30_000,
  });
  const month = useCardData(`agent-usage-30d-${project}-${agent.name}`, () => getAgentUsage(project, agent.name, "30d"));
  const loading = today.loading && !today.data;
  if (!loading && !today.data) return <LoadError message={t("error")} />;
  const u = today.data;
  const used = u ? u.tokens_in + u.tokens_out : 0;
  const budget = u ? u.budget || agent.daily_token_budget : agent.daily_token_budget;
  const pct = budget > 0 ? Math.round((used / budget) * 100) : 0;
  const kpis = [
    { label: t("costToday"), value: u && f.eur(u.cost_eur) },
    { label: t("costMonth"), value: month.data && f.eur(month.data.cost_eur) },
    { label: t("requests"), value: u && f.tokens(u.requests) },
    { label: t("errorRate"), value: u && `${(u.requests ? (u.errors / u.requests) * 100 : 0).toFixed(1)} %` },
  ];
  const pulse = <span className="inline-block h-5 w-16 animate-pulse rounded bg-muted align-middle" aria-hidden />;
  return (
    <div className="space-y-4" aria-busy={loading}>
      <div className="grid gap-4 lg:grid-cols-[auto_minmax(0,1fr)]">
        <div className={cn(panel, "flex items-center gap-5")}>
          {loading ? (
            <div className="size-[136px] shrink-0 animate-pulse rounded-full bg-muted" aria-hidden />
          ) : (
            <RingGauge
              value={used}
              max={budget}
              size={136}
              label={t("budget")}
              valueText={t("budgetValue", { used: f.tokens(used), budget: f.tokens(budget) })}
            >
              <span>
                <span className="block text-2xl font-semibold tabular-nums">{pct} %</span>
                <span className="text-xs text-muted-foreground">{t("ofBudget")}</span>
              </span>
            </RingGauge>
          )}
          <div className="space-y-1">
            <h2 className="text-sm font-semibold">{t("budget")}</h2>
            <p className="text-2xl font-semibold tabular-nums">{loading ? pulse : f.tokens(used)}</p>
            <p className="text-sm text-muted-foreground">/ {f.tokens(budget)} {t("tokensPerDay")}</p>
            <p className="text-xs text-muted-foreground">{t("hardCap")}</p>
            {u?.resets_at && (
              <p className="text-xs text-muted-foreground">{t("resetsAt", { time: f.clock(u.resets_at) })}</p>
            )}
          </div>
        </div>
        <div className={cn(panel, "flex flex-col gap-3")}>
          <h2 className="text-sm font-semibold">{t("activity")}</h2>
          {u ? (
            <Sparkline points={u.series.map((p) => p.tokens)} className="h-24 w-full" />
          ) : (
            <div className="h-24 w-full animate-pulse rounded-lg bg-muted" aria-hidden />
          )}
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {kpis.map((k) => (
              <div key={k.label}>
                <dt className="text-xs text-muted-foreground">{k.label}</dt>
                <dd className="text-lg font-semibold tabular-nums">{k.value ?? pulse}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <div className={panel}>
        <h2 className="mb-3 text-sm font-semibold">{t("refusals", { count: u?.refused_total ?? 0 })}</h2>
        <ul className="grid gap-2 sm:grid-cols-4">
          {REASONS.map((reason) => (
            <li key={reason} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
              <span>{tReason(reason)}</span>
              {u ? (
                <span className={cn("font-semibold tabular-nums", (u.refused[reason] ?? 0) > 0 && "text-destructive")}>
                  {u.refused[reason] ?? 0}
                </span>
              ) : (
                <span className="h-4 w-6 animate-pulse rounded bg-muted" aria-hidden />
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function AgentTraces() {
  const t = useTranslations("ai.traces");
  const f = useAiFormat();
  const { project, agent } = useAgentContext();
  const traces = useCardData(`agent-traces-${project}-${agent.name}`, () => getAgentTraces(project, agent.name), {
    intervalMs: 30_000,
  });
  const loading = traces.loading && !traces.data;
  if (!loading && !traces.data) return <LoadError message={t("error")} />;
  const grafana_url = traces.data?.grafana_url;
  const columns: DataColumn<AgentTrace>[] = [
    {
      key: "start",
      header: t("when"),
      sortable: true,
      sortValue: (trace) => Date.parse(trace.start),
      className: "whitespace-nowrap text-muted-foreground",
      cell: (trace) => f.relative(trace.start),
    },
    {
      key: "root_operation",
      header: t("task"),
      sortable: true,
      className: "max-w-md truncate",
      cell: (trace) => trace.root_operation,
    },
    {
      key: "errors",
      header: t("status"),
      sortable: true,
      cell: (trace) => (
        <Badge
          variant="secondary"
          className={
            trace.errors === 0
              ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300"
              : "bg-destructive/10 text-destructive"
          }
        >
          {trace.errors === 0 ? t("statuses.ok") : t("errors", { count: trace.errors })}
        </Badge>
      ),
    },
    {
      key: "duration_ms",
      header: t("duration"),
      align: "right",
      sortable: true,
      cell: (trace) => f.ms(trace.duration_ms),
    },
    { key: "spans", header: t("spans"), align: "right", sortable: true, cell: (trace) => trace.spans },
    {
      key: "trace_id",
      header: t("traceId"),
      className: "font-mono text-xs text-muted-foreground",
      cell: (trace) => trace.trace_id.slice(0, 16),
    },
  ];
  return (
    <div className="space-y-3">
      {grafana_url && (
        <a href={grafana_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
          {t("grafana")}
          <ExternalLink className="size-3.5" aria-hidden />
        </a>
      )}
      <DataTable
        columns={columns}
        rows={traces.data?.items ?? []}
        rowKey={(trace) => trace.trace_id}
        loading={loading}
        storageKey="agent.traces"
        empty={{ title: t("empty") }}
      />
    </div>
  );
}

export function AgentCapabilities() {
  const t = useTranslations("ai.capabilities");
  const { agent, registry } = useAgentContext();
  const nodes: CapabilityNode[] = [{ name: agent.name, model: agent.model, tools: agent.tools, isolation: agent.isolation ?? undefined }];
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">{t("hint")}</p>
      <CapabilityMap nodes={nodes} registry={registry} wide />
      {agent.calls.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {t("calls")} {agent.calls.map((c) => <code key={c} className="mr-1.5 font-mono text-xs">{c}</code>)}
        </p>
      )}
    </div>
  );
}

export function AgentAccess() {
  const t = useTranslations("ai.access");
  const { project, agent, registry } = useAgentContext();
  const card = {
    name: agent.name,
    description: agent.description,
    url: `/a2a/${project}/${agent.name}`,
    skills: agent.tools,
  };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section aria-labelledby="access-tools" className={cn(panel, "space-y-3 lg:row-span-2")}>
        <h2 id="access-tools" className="text-sm font-semibold">{t("tools")}</h2>
        <ul className="space-y-2">
          {agent.tools.map((name) => {
            const tool = registry.find((x) => x.name === name);
            return (
              <li key={name} className="flex flex-wrap items-center gap-2 rounded-lg border p-2.5">
                <span className="min-w-0 flex-1">
                  <span className="block font-mono text-xs font-semibold">{name}</span>
                  {tool?.description && <span className="block text-xs text-muted-foreground">{tool.description}</span>}
                </span>
                {tool?.badges.map((risk) => <RiskBadge key={risk} risk={risk} />)}
              </li>
            );
          })}
        </ul>
      </section>
      <section aria-labelledby="access-callers" className={cn(panel, "space-y-3")}>
        <h2 id="access-callers" className="flex items-center gap-2 text-sm font-semibold">
          <Users className="size-4" aria-hidden />
          {t("callers")}
        </h2>
        <ul className="flex flex-wrap gap-1.5">
          {agent.callers.map((caller) => (
            <li key={caller}><Badge variant="outline" className="font-mono">{caller}</Badge></li>
          ))}
        </ul>
        {agent.client_id && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" aria-hidden />
            {t("client")} <code className="font-mono">{agent.client_id}</code>
          </p>
        )}
      </section>
      <section aria-labelledby="access-card" className={cn(panel, "space-y-3")}>
        <h2 id="access-card" className="text-sm font-semibold">{t("agentCard")}</h2>
        <pre tabIndex={0} className="max-h-64 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-xs">
          {JSON.stringify(card, null, 2)}
        </pre>
      </section>
    </div>
  );
}

/** The activity log entries about this agent: settings changes, suspensions, tests. */
export function AgentJournal() {
  const t = useTranslations("ai.journal");
  const fmt = useFormat();
  const { project, agent } = useAgentContext();
  const journal = useCardData(`agent-journal-${project}-${agent.name}-${agent.status}`, async () => {
    const feed = await getActivity(project, { days: 30, include_reads: false });
    const path = `/agents/${agent.name}`;
    return feed.events.filter(
      (event) =>
        event.actor === agent.client_id ||
        (event.action.startsWith("ai.agent.") && (event.target.endsWith(path) || event.target.includes(`${path}/`))),
    );
  });
  if (journal.loading && !journal.data) return <Skeleton className="h-56 rounded-xl" />;
  if (!journal.data) return <LoadError message={t("error")} />;
  if (journal.data.length === 0) return <p className="py-10 text-center text-sm text-muted-foreground">{t("empty")}</p>;
  return (
    <ol className={cn(panel, "space-y-0 divide-y p-0")}>
      {journal.data.map((entry) => (
        <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-3 text-sm">
          <time dateTime={entry.time} className="w-40 shrink-0 text-xs text-muted-foreground">{fmt.dateTime(entry.time)}</time>
          <span className="font-medium">{t.has(`actions.${entry.action}`) ? t(`actions.${entry.action}`) : entry.action}</span>
          <span className="text-muted-foreground">{entry.actor}</span>
          {entry.outcome === "failure" && <Badge variant="destructive">{t("failed")}</Badge>}
        </li>
      ))}
    </ol>
  );
}
