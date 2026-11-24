"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { AlertCircle, Bot, Loader2, Power, PowerOff } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AgentCard, AgentCardSkeleton, agentHref } from "./AgentCard";
import { AgentStatusBadge, BarGauge, IsolationBadge, RiskBadge, RingGauge, useAiFormat } from "./shared";
import { TemplatesGallery } from "./TemplatesGallery";

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
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy>
        {[0, 1, 2].map((i) => <AgentCardSkeleton key={i} />)}
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
  const loading = agents.loading && !agents.data;
  const rows = useMemo(
    () => (agents.data ?? []).map((a) => overrides[`${a.project}/${a.name}`] ?? a),
    [agents.data, overrides],
  );

  if (!loading && !agents.data) return <Failed message={t("error")} />;
  const active = rows.filter((a) => a.status === "active").length;
  const tokens = rows.reduce((s, a) => s + (a.usage_today?.tokens ?? 0), 0);
  const cost = rows.reduce((s, a) => s + (a.usage_today?.cost_eur ?? 0), 0);
  const columns: DataColumn<Agent>[] = [
    {
      key: "name",
      header: t("colAgent"),
      sortable: true,
      cell: (agent) => (
        <Link href={agentHref(agent.project, agent.name)} className="whitespace-nowrap font-mono font-semibold hover:underline">
          {agent.name}
        </Link>
      ),
    },
    { key: "project", header: t("colProject"), sortable: true, cell: (agent) => agent.project },
    { key: "owner", header: t("colOwner"), sortable: true, className: "text-muted-foreground", cell: (agent) => agent.owner },
    {
      key: "isolation",
      header: t("colIsolation"),
      cell: (agent) => agent.isolation && <IsolationBadge isolation={agent.isolation} />,
    },
    {
      key: "tokens",
      header: t("colTokens"),
      sortable: true,
      sortValue: (agent) => agent.usage_today?.tokens ?? 0,
      className: "w-48",
      cell: (agent) => (
        <div className="space-y-1">
          <BarGauge value={agent.usage_today?.tokens ?? 0} max={agent.daily_token_budget} label={t("budgetOf", { name: agent.name })} />
          <span className="text-xs tabular-nums text-muted-foreground">
            {f.tokens(agent.usage_today?.tokens ?? 0)} / {f.tokens(agent.daily_token_budget)}
          </span>
        </div>
      ),
    },
    { key: "status", header: t("colStatus"), sortable: true, cell: (agent) => <AgentStatusBadge status={agent.status} /> },
    {
      key: "updated_at",
      header: t("colLast"),
      sortable: true,
      className: "whitespace-nowrap text-muted-foreground",
      cell: (agent) => (agent.updated_at ? f.relative(agent.updated_at) : t("never")),
    },
    {
      key: "actions",
      header: <span className="sr-only">{t("colActions")}</span>,
      align: "right",
      cell: (agent) => (
        <RowKill agent={agent} onChange={(a) => setOverrides((o) => ({ ...o, [`${a.project}/${a.name}`]: a }))} />
      ),
    },
  ];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          [t("kpiAgents"), String(rows.length)],
          [t("kpiActive"), String(active)],
          [t("kpiTokens"), f.tokens(tokens)],
          [t("kpiCost"), f.eur(cost)],
        ].map(([label, value]) => (
          <div key={label} className={cn(panel, "px-4 py-3")}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-2xl font-semibold tabular-nums">
              {loading ? <span className="inline-block h-7 w-16 animate-pulse rounded bg-muted align-middle" aria-hidden /> : value}
            </dd>
          </div>
        ))}
      </dl>
      <DataTable
        caption={t("tableCaption")}
        columns={columns}
        rows={rows}
        rowKey={(agent) => `${agent.project}/${agent.name}`}
        loading={loading}
        storageKey="agents.all"
        searchable
        searchKeys={["name", "project", "owner"]}
        searchPlaceholder={t("search")}
      />
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
        <Tabs defaultValue="mine">
          <TabsList className="h-auto flex-wrap">
            <TabsTrigger value="mine">{t("tabMine")}</TabsTrigger>
            <TabsTrigger value="templates">{t("tabTemplates")}</TabsTrigger>
            {isAdmin && (
              <>
                <TabsTrigger value="all">{t("tabAll")}</TabsTrigger>
                <TabsTrigger value="mcp">{t("tabMcp")}</TabsTrigger>
                <TabsTrigger value="models">{t("tabModels")}</TabsTrigger>
              </>
            )}
          </TabsList>
          <TabsContent value="mine" className="pt-4"><MyAgents /></TabsContent>
          <TabsContent value="templates" className="pt-4"><TemplatesGallery /></TabsContent>
          {isAdmin && (
            <>
              <TabsContent value="all" className="pt-4"><AllAgents /></TabsContent>
              <TabsContent value="mcp" className="pt-4"><McpServers /></TabsContent>
              <TabsContent value="models" className="pt-4"><Models /></TabsContent>
            </>
          )}
        </Tabs>
      </PageBody>
    </div>
  );
}
