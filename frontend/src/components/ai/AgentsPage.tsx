"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getAdminAgents,
  getAdminMcpServers,
  getAiBackends,
  getAiBudget,
  getProjectAgents,
  resumeAgent,
  suspendAgent,
} from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import { useCardData } from "@/lib/useCardData";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { cn } from "@/lib/utils";
import type { Agent } from "@/types/ai";
import { AlertCircle, Bot, Loader2, Power, PowerOff, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { AgentCard, agentHref } from "./AgentCard";
import { AgentStatusBadge, BarGauge, IsolationBadge, RiskBadge, RingGauge, useAiFormat } from "./shared";

const panel = "rounded-xl bg-card ring-1 ring-foreground/10";

function Failed({ message }: { message: string }) {
  return (
    <p className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
      <AlertCircle className="size-4" aria-hidden />
      {message}
    </p>
  );
}

function GlobalBudget() {
  const t = useTranslations("ai.agents");
  const f = useAiFormat();
  const budget = useCardData("ai-budget", getAiBudget, { intervalMs: 30_000 });
  if (!budget.data) return budget.loading ? <Skeleton className="h-28 w-72 rounded-xl" /> : null;
  const { spend_usd, budget_usd: cap_usd, ratio, status } = budget.data;
  const pct = ratio * 100;
  return (
    <div className={cn(panel, "flex items-center gap-4 p-4")}>
      <RingGauge
        value={spend_usd}
        max={cap_usd}
        size={96}
        label={t("globalBudget")}
        valueText={t("globalValue", { spent: f.usd(spend_usd), cap: f.usd(cap_usd) })}
      >
        <span className="text-sm font-semibold tabular-nums">{Math.round(pct)} %</span>
      </RingGauge>
      <div className="space-y-0.5">
        <p className="text-sm font-semibold">{t("globalBudget")}</p>
        <p className="text-xl font-semibold tabular-nums">
          {f.usd(spend_usd)} <span className="text-sm font-normal text-muted-foreground">/ {f.usd(cap_usd)}</span>
        </p>
        <p className={cn("text-xs", status !== "ok" ? "text-destructive" : "text-muted-foreground")}>
          {status === "exhausted" ? t("globalExhausted") : status === "warning" ? t("globalWarn") : t("globalHint")}
        </p>
      </div>
    </div>
  );
}

function MyAgents() {
  const t = useTranslations("ai.agents");
  const { projects, loading } = useProjects();
  const names = projects.map((p) => p.name).sort();
  const agents = useCardData(
    `my-agents-${names.join(",")}`,
    async () => (await Promise.allSettled(names.map((p) => getProjectAgents(p)))).flatMap((r) => (r.status === "fulfilled" ? r.value : [])),
    { enabled: !loading, intervalMs: 30_000 },
  );
  if (loading || (agents.loading && !agents.data)) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-44 rounded-xl" />)}
      </div>
    );
  }
  if (!agents.data || agents.data.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-12 text-center">
        <Bot className="size-8 text-primary" aria-hidden />
        <p className="font-medium">{t("noneTitle")}</p>
        <p className="max-w-md text-sm text-muted-foreground">{t("noneBody")}</p>
      </div>
    );
  }
  return (
    <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {agents.data.map((agent) => (
        <li key={`${agent.project}/${agent.name}`} className="contents">
          <AgentCard agent={agent} />
        </li>
      ))}
    </ul>
  );
}

function RowKill({ agent, onChange }: { agent: Agent; onChange: (a: Agent) => void }) {
  const t = useTranslations("ai.killSwitch");
  const [busy, setBusy] = useState(false);
  const suspended = agent.status === "suspended";
  const flip = async () => {
    setBusy(true);
    try {
      const next = await (suspended ? resumeAgent : suspendAgent)(agent.project, agent.name);
      onChange(next);
      toast.success(t(suspended ? "resumed" : "suspended", { name: agent.name }));
    } catch (err) {
      toast.error(t("failed"), { description: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button
      size="sm"
      variant={suspended ? "outline" : "destructive"}
      onClick={() => void flip()}
      disabled={busy}
      aria-label={t(suspended ? "resumeNamed" : "suspendNamed", { name: agent.name })}
    >
      {busy ? <Loader2 className="animate-spin" aria-hidden /> : suspended ? <Power aria-hidden /> : <PowerOff aria-hidden />}
      {suspended ? t("resume") : t("suspendShort")}
    </Button>
  );
}

function AllAgents() {
  const t = useTranslations("ai.agents");
  const f = useAiFormat();
  const agents = useCardData("admin-agents", getAdminAgents, { intervalMs: 30_000 });
  const [overrides, setOverrides] = useState<Record<string, Agent>>({});
  const [query, setQuery] = useState("");
  const searchId = useId();
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (agents.data ?? [])
      .map((a) => overrides[`${a.project}/${a.name}`] ?? a)
      .filter((a) => !q || `${a.name} ${a.project} ${a.owner}`.toLowerCase().includes(q));
  }, [agents.data, overrides, query]);

  if (agents.loading && !agents.data) return <Skeleton className="h-64 rounded-xl" />;
  if (!agents.data) return <Failed message={t("error")} />;
  const active = agents.data.filter((a) => a.status === "active").length;
  const tokens = agents.data.reduce((s, a) => s + (a.usage_today?.tokens ?? 0), 0);
  const cost = agents.data.reduce((s, a) => s + (a.usage_today?.cost_eur ?? 0), 0);
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          [t("kpiAgents"), String(agents.data.length)],
          [t("kpiActive"), String(active)],
          [t("kpiTokens"), f.tokens(tokens)],
          [t("kpiCost"), f.eur(cost)],
        ].map(([label, value]) => (
          <div key={label} className={cn(panel, "px-4 py-3")}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="relative max-w-sm">
        <label htmlFor={searchId} className="sr-only">{t("search")}</label>
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input id={searchId} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("search")} className="h-9 pl-8" />
      </div>
      <div className={cn(panel, "overflow-x-auto")} tabIndex={0} role="region" aria-label={t("tableCaption")}>
        <table className="w-full min-w-[880px] text-sm">
          <caption className="sr-only">{t("tableCaption")}</caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="px-4 py-2.5 font-medium">{t("colAgent")}</th>
              <th scope="col" className="px-4 py-2.5 font-medium">{t("colProject")}</th>
              <th scope="col" className="px-4 py-2.5 font-medium">{t("colOwner")}</th>
              <th scope="col" className="px-4 py-2.5 font-medium">{t("colIsolation")}</th>
              <th scope="col" className="w-48 px-4 py-2.5 font-medium">{t("colTokens")}</th>
              <th scope="col" className="px-4 py-2.5 font-medium">{t("colStatus")}</th>
              <th scope="col" className="px-4 py-2.5 font-medium">{t("colLast")}</th>
              <th scope="col" className="px-4 py-2.5"><span className="sr-only">{t("colActions")}</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((agent) => (
              <tr key={`${agent.project}/${agent.name}`} className="border-b last:border-0">
                <td className="px-4 py-2.5">
                  <Link href={agentHref(agent.project, agent.name)} className="whitespace-nowrap font-mono font-semibold hover:underline">
                    {agent.name}
                  </Link>
                </td>
                <td className="px-4 py-2.5">{agent.project}</td>
                <td className="px-4 py-2.5 text-muted-foreground">{agent.owner}</td>
                <td className="px-4 py-2.5"><IsolationBadge isolation={agent.isolation} /></td>
                <td className="px-4 py-2.5">
                  <div className="space-y-1">
                    <BarGauge value={agent.usage_today?.tokens ?? 0} max={agent.daily_token_budget} label={t("budgetOf", { name: agent.name })} />
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {f.tokens(agent.usage_today?.tokens ?? 0)} / {f.tokens(agent.daily_token_budget)}
                    </span>
                  </div>
                </td>
                <td className="px-4 py-2.5"><AgentStatusBadge status={agent.status} /></td>
                <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">
                  {agent.updated_at ? f.relative(agent.updated_at) : t("never")}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <RowKill agent={agent} onChange={(a) => setOverrides((o) => ({ ...o, [`${a.project}/${a.name}`]: a }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">{t("killHint")}</p>
    </div>
  );
}

const SERVER_TONE = {
  approved: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  pending: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  retired: "",
} as const;

function McpServers() {
  const t = useTranslations("ai.agents");
  const servers = useCardData("admin-mcp", getAdminMcpServers);
  if (servers.loading && !servers.data) return <Skeleton className="h-64 rounded-xl" />;
  if (!servers.data) return <Failed message={t("error")} />;
  return (
    <ul className="grid gap-4 lg:grid-cols-2">
      {servers.data.map((server) => (
        <li key={`${server.project}/${server.name}`} className={cn(panel, "space-y-3 p-4")}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="font-mono text-sm font-semibold">{server.name}</p>
              <p className="text-xs text-muted-foreground">
                {server.project ? t("serverProject", { project: server.project }) : t("serverPlatform")} · {server.source}
              </p>
            </div>
            <Badge variant="secondary" className={SERVER_TONE[server.status]}>{t(`serverStatus.${server.status}`)}</Badge>
          </div>
          <ul className="space-y-1.5">
            {server.tools.map((tool) => (
              <li key={tool.name} className="flex flex-wrap items-center gap-2 rounded-lg border px-2.5 py-1.5">
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{tool.name}</span>
                {tool.badges.map((risk) => <RiskBadge key={risk} risk={risk} />)}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

function Models() {
  const t = useTranslations("ai.agents");
  const tb = useTranslations("ai.backend");
  const f = useAiFormat();
  const backends = useCardData("ai-backends", getAiBackends, { intervalMs: 60_000 });
  if (backends.loading && !backends.data) return <Skeleton className="h-48 rounded-xl" />;
  if (!backends.data) return <Failed message={t("error")} />;
  return (
    <div className="space-y-4">
      {backends.data.items.map((backend) => (
        <section key={backend.name} aria-label={backend.name} className={cn(panel, "space-y-4 p-4")}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold capitalize">{backend.name}</p>
            <Badge variant="secondary">{tb(backend.status)}</Badge>
          </div>
          <div className="space-y-1.5">
            <p className="text-sm tabular-nums">
              {tb("spend", { spent: f.usd(backend.spend_usd), limit: f.usd(backend.spend_limit_usd) })}
            </p>
            <BarGauge value={backend.spend_usd} max={backend.spend_limit_usd} label={tb("spendLabel", { name: backend.name })} />
          </div>
        </section>
      ))}
    </div>
  );
}

export function AgentsPage() {
  const t = useTranslations("ai.agents");
  const isAdmin = usePlatformAdmin();
  return (
    <div className="min-h-screen">
      <PageHeader title={t("title")} description={isAdmin ? t("descriptionAdmin") : t("description")} icon={Bot}>
        {isAdmin && <div className="mt-4"><GlobalBudget /></div>}
      </PageHeader>
      <PageBody>
        {isAdmin ? (
          <Tabs defaultValue="mine">
            <TabsList className="h-auto flex-wrap">
              <TabsTrigger value="mine">{t("tabMine")}</TabsTrigger>
              <TabsTrigger value="all">{t("tabAll")}</TabsTrigger>
              <TabsTrigger value="mcp">{t("tabMcp")}</TabsTrigger>
              <TabsTrigger value="models">{t("tabModels")}</TabsTrigger>
            </TabsList>
            <TabsContent value="mine" className="pt-4"><MyAgents /></TabsContent>
            <TabsContent value="all" className="pt-4"><AllAgents /></TabsContent>
            <TabsContent value="mcp" className="pt-4"><McpServers /></TabsContent>
            <TabsContent value="models" className="pt-4"><Models /></TabsContent>
          </Tabs>
        ) : (
          <MyAgents />
        )}
      </PageBody>
    </div>
  );
}
