"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { projectHref } from "@/components/layout/projectSections";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getAgent, getProjectAiTools, reconcileProjectAi, resumeAgent, suspendAgent } from "@/lib/api";
import { answersFromTools, riskProfile } from "@/lib/ai/risk";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { Agent, AgentDetail, AiTool } from "@/types/ai";
import {
  Activity,
  Bot,
  FileWarning,
  FlaskConical,
  GitPullRequest,
  KeyRound,
  LayoutDashboard,
  Loader2,
  Network,
  Power,
  PowerOff,
  ScrollText,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { createContext, useCallback, useContext, useState } from "react";
import { toast } from "sonner";
import { agentHref } from "./AgentCard";
import { AgentStatusBadge, IsolationBadge, ProvisionBadge } from "./shared";

interface AgentContextValue {
  project: string;
  agent: AgentDetail;
  registry: AiTool[];
  setAgent: (agent: Agent) => void;
}

const AgentContext = createContext<AgentContextValue | null>(null);

export function useAgentContext(): AgentContextValue {
  const value = useContext(AgentContext);
  if (!value) throw new Error("useAgentContext outside AgentFrame");
  return value;
}

/**
 * The kill switch. Suspending takes one click, no dialog: in an incident every
 * second counts and resuming undoes it.
 */
function KillSwitch({ agent, onChange }: { agent: Agent; onChange: (agent: Agent) => void }) {
  const t = useTranslations("ai.killSwitch");
  const [pending, setPending] = useState<"suspend" | "resume" | null>(null);
  const [announce, setAnnounce] = useState("");
  const suspended = agent.status === "suspended";

  const flip = async () => {
    const action = suspended ? "resume" : "suspend";
    setPending(action);
    setAnnounce(t(action === "suspend" ? "suspending" : "resuming"));
    try {
      const next = await (action === "suspend" ? suspendAgent : resumeAgent)(agent.project, agent.name);
      onChange(next);
      const message = t(action === "suspend" ? "suspended" : "resumed", { name: agent.name });
      setAnnounce(message);
      toast.success(message);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setAnnounce(t("failed"));
      toast.error(t("failed"), { description: message });
    } finally {
      setPending(null);
    }
  };

  return (
    <>
      <Button
        size="lg"
        onClick={() => void flip()}
        disabled={pending !== null}
        aria-pressed={suspended}
        className={cn(
          "h-10 px-4 font-semibold shadow-sm transition-all",
          suspended
            ? "bg-emerald-700 text-white hover:bg-emerald-800 active:bg-emerald-800"
            : "bg-red-700 text-white shadow-red-700/30 hover:bg-red-800 active:bg-red-800",
        )}
      >
        {pending ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : suspended ? (
          <Power aria-hidden />
        ) : (
          <PowerOff aria-hidden />
        )}
        {pending ? t(pending === "suspend" ? "suspending" : "resuming") : suspended ? t("resume") : t("suspend")}
      </Button>
      <span role="status" aria-live="assertive" className="sr-only">{announce}</span>
    </>
  );
}

/**
 * An agent declared in Git before its credentials existed. Provisioning runs
 * the project reconcile, which also covers its other agents and AI apps.
 */
function ProvisionBanner({ agent, canProvision, onDone }: { agent: Agent; canProvision: boolean; onDone: () => void }) {
  const t = useTranslations("ai.provision");
  const [busy, setBusy] = useState(false);
  const provision = async () => {
    setBusy(true);
    try {
      const report = await reconcileProjectAi(agent.project);
      const mine = report.items.find((i) => i.kind === "agent" && i.name === agent.name);
      if (mine?.error) throw new Error(mine.error);
      toast.success(t("done", { name: agent.name }));
      onDone();
    } catch (err) {
      toast.error(t("failed"), { description: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="border-b border-primary/30 bg-primary/5">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-6 py-2.5 text-sm">
        <KeyRound className="size-4 shrink-0 text-primary" aria-hidden />
        <p className="min-w-0 flex-1">{t("banner")}</p>
        {canProvision && (
          <Button size="sm" onClick={() => void provision()} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <KeyRound aria-hidden />}
            {busy ? t("running") : t("action")}
          </Button>
        )}
      </div>
    </div>
  );
}

export function AgentFrame({ agentName, children }: { agentName: string; children: React.ReactNode }) {
  const t = useTranslations("ai.agentPage");
  const tNav = useTranslations("projectNav");
  const tRisk = useTranslations("ai.ruleOfTwo");
  const { projectName, can } = useProjectContext();
  const loaded = useCardData(`agent-${projectName}-${agentName}`, () => getAgent(projectName, agentName), {
    intervalMs: 20_000,
  });
  const tools = useCardData(`ai-tools-${projectName}`, () => getProjectAiTools(projectName));
  // A suspend or resume answer wins until the next poll brings a fresher read.
  const [local, setLocal] = useState<{ agent: AgentDetail; base: AgentDetail | null } | null>(null);
  const agent = local && local.base === loaded.data ? local.agent : loaded.data;
  const setAgent = useCallback(
    (next: Agent) => setLocal({ agent: { ...loaded.data, ...next }, base: loaded.data }),
    [loaded.data],
  );

  const base = agentHref(projectName, agentName);
  const tabs = [
    { href: base, label: t("tabs.overview"), icon: LayoutDashboard, exact: true },
    // Testing and traces are member reads: the auditor holds ai.view, not these.
    ...(can("app.operate") ? [{ href: `${base}/test`, label: t("tabs.test"), icon: FlaskConical }] : []),
    ...(can("app.viewTraces") ? [{ href: `${base}/traces`, label: t("tabs.traces"), icon: Activity }] : []),
    { href: `${base}/capabilities`, label: t("tabs.capabilities"), icon: Network },
    { href: `${base}/access`, label: t("tabs.access"), icon: KeyRound },
    { href: `${base}/journal`, label: t("tabs.journal"), icon: ScrollText },
  ];
  const crumbs = [
    { label: tNav("projects"), href: "/projects" },
    { label: projectName, href: projectHref(projectName) },
    { label: tNav("ai"), href: projectHref(projectName, "ai") },
    { label: agentName },
  ];

  if (!agent) {
    return (
      <div className="min-h-screen">
        <PageHeader title={agentName} icon={Bot} breadcrumbs={crumbs} />
        <PageBody>
          {loaded.loading || (!loaded.error && !loaded.forbidden) ? (
            <Skeleton className="h-64 w-full rounded-xl" />
          ) : (
            <p role="alert" className="rounded-xl border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
              {t("loadError")}
            </p>
          )}
        </PageBody>
      </div>
    );
  }

  const riskCount = agent.risk?.count ?? riskProfile(answersFromTools(agent.tools, tools.data ?? [])).lit.length;
  return (
    <AgentContext.Provider value={{ project: projectName, agent, registry: tools.data ?? [], setAgent }}>
      <div className="min-h-screen">
        <PageHeader
          title={<span className="font-mono">{agent.name}</span>}
          icon={Bot}
          breadcrumbs={crumbs}
          tabs={tabs}
          tabsLabel={t("tabsLabel")}
          actions={can("agent.manage") && <KillSwitch agent={agent} onChange={setAgent} />}
        >
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <AgentStatusBadge status={agent.status} />
            {agent.credentials === "missing" && <ProvisionBadge />}
            {agent.pull_request?.state === "open" && (
              <a
                href={agent.pull_request.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-5 items-center gap-1 rounded-full border border-primary/40 px-2 text-xs font-medium text-primary hover:bg-primary/10"
              >
                <GitPullRequest className="size-3" aria-hidden />
                {t("prOpen", { number: agent.pull_request.number })}
              </a>
            )}
            {agent.isolation && <IsolationBadge isolation={agent.isolation} />}
            <Badge
              variant="secondary"
              className={riskCount >= 2 ? "bg-amber-500/15 text-amber-800 dark:text-amber-300" : ""}
            >
              {tRisk("verdict", { count: riskCount })}
            </Badge>
            {agent.owner && (
              <>
                <span>{t("owner", { owner: agent.owner })}</span>
                <span aria-hidden>·</span>
              </>
            )}
            <span className="font-mono">{agent.model}</span>
          </div>
        </PageHeader>
        {agent.credentials === "missing" && (
          <ProvisionBanner
            agent={agent}
            canProvision={can("ai.configure")}
            onDone={() => setAgent({ ...agent, credentials: "ok" })}
          />
        )}
        {agent.settings?.message && (
          <div className="border-b border-amber-500/30 bg-amber-500/10">
            <p className="mx-auto flex max-w-7xl items-center gap-2 px-6 py-2.5 text-sm text-amber-900 dark:text-amber-200">
              <FileWarning className="size-4 shrink-0" aria-hidden />
              {t("settingsMissing", { path: agent.values_path ?? "", repo: agent.repo ?? "" })}
            </p>
          </div>
        )}
        {agent.status === "suspended" && (
          <div className="border-b border-amber-500/30 bg-amber-500/10">
            <p className="mx-auto flex max-w-7xl items-center gap-2 px-6 py-2.5 text-sm text-amber-900 dark:text-amber-200">
              <PowerOff className="size-4 shrink-0" aria-hidden />
              {t("suspendedBanner")}
            </p>
          </div>
        )}
        <PageBody>{children}</PageBody>
      </div>
    </AgentContext.Provider>
  );
}
