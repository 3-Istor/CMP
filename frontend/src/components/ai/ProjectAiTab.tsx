"use client";

import { ProjectAgentPanel } from "@/components/agents/ProjectAgentPanel";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  getAiBackends,
  getProjectAgents,
  getProjectAi,
  getProjectAiUsage,
  grantAppAi,
  putProjectAi,
} from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { AiBackend, AiUsageItem, ProjectAi } from "@/types/ai";
import { AlertCircle, Bot, CloudOff, Cpu, Plug, Plus, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useId, useState } from "react";
import { toast } from "sonner";
import { AgentCard } from "./AgentCard";
import { BarGauge, RingGauge, useAiFormat } from "./shared";
import { projectHref } from "@/components/layout/projectSections";

const ALERT_PCT = 80;

function SectionTitle({ id, icon: Icon, title, hint, action }: {
  id: string;
  icon: typeof Bot;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex items-center gap-2.5">
        <span className="grid size-8 place-items-center rounded-lg bg-primary/10 text-primary">
          <Icon className="size-4" aria-hidden />
        </span>
        <div>
          <h2 id={id} className="text-base font-semibold tracking-tight">{title}</h2>
          {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

const panel = "rounded-xl bg-card p-4 ring-1 ring-foreground/10";

export function usageTotals(items: AiUsageItem[]) {
  return items.reduce(
    (acc, item) => ({
      tokens: acc.tokens + item.tokens_in + item.tokens_out,
      cost: acc.cost + item.cost_eur,
      refused: acc.refused + item.refused_total,
    }),
    { tokens: 0, cost: 0, refused: 0 },
  );
}

function AccessCard({ ai, onChange }: { ai: ProjectAi; onChange: (next: ProjectAi) => void }) {
  const t = useTranslations("ai.tab");
  const { projectName, can } = useProjectContext();
  const [busy, setBusy] = useState(false);
  const switchId = useId();
  const canConfigure = can("ai.configure") && (ai.can_enable || ai.enabled);

  const toggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      const body = {
        enabled,
        daily_token_budget: ai.daily_token_budget,
        models: ai.models,
        web_search: ai.web_search,
      };
      onChange({ ...ai, ...(await putProjectAi(projectName, body)) });
      toast.success(enabled ? t("enabledToast") : t("disabledToast"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cn(panel, "flex flex-col gap-3")}>
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={switchId} className="text-sm font-semibold">{t("accessTitle")}</Label>
        <Switch
          id={switchId}
          checked={ai.enabled}
          disabled={!canConfigure || busy}
          onCheckedChange={(checked) => void toggle(checked)}
        />
      </div>
      <p className="text-sm text-muted-foreground">{t("accessBody")}</p>
      {!ai.can_enable ? (
        <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          {t("criticalBody")}
        </p>
      ) : (
        <Badge
          variant="secondary"
          className={ai.enabled ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : ""}
        >
          {ai.enabled ? t("enabled") : t("disabled")}
        </Badge>
      )}
    </div>
  );
}

const BACKEND_TONE: Record<AiBackend["status"], string> = {
  ok: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  unavailable: "bg-destructive/10 text-destructive",
  spend_limit_reached: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
};

function ModelsCard({ ai }: { ai: ProjectAi }) {
  const t = useTranslations("ai.tab");
  const tb = useTranslations("ai.backend");
  const f = useAiFormat();
  const backends = useCardData("ai-backends", getAiBackends, { intervalMs: 60_000 });
  return (
    <div className={cn(panel, "flex flex-col gap-3")}>
      <h3 className="text-sm font-semibold">{t("modelsTitle")}</h3>
      <ul className="flex flex-wrap gap-1.5" aria-label={t("modelsTitle")}>
        {ai.models.map((model) => (
          <li key={model}>
            <Badge variant="outline" className="font-mono">
              <Cpu aria-hidden />
              {model}
            </Badge>
          </li>
        ))}
      </ul>
      <div className="mt-auto space-y-1.5" aria-live="polite">
        {backends.loading && !backends.data ? (
          <Skeleton className="h-6 w-40" />
        ) : (
          (backends.data?.items ?? []).map((backend) => (
            <div key={backend.name} className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-medium capitalize">{backend.name}</span>
              <Badge variant="secondary" className={BACKEND_TONE[backend.status]}>
                <span aria-hidden className="size-1.5 rounded-full bg-current" />
                {tb(backend.status)}
              </Badge>
            </div>
          ))
        )}
        {backends.error && !backends.data && (
          <p className="text-xs text-muted-foreground">{tb("unknown")}</p>
        )}
        {backends.data?.items[0] && (
          <p className="text-xs text-muted-foreground">
            {tb("spend", {
              spent: f.usd(backends.data.items[0].spend_usd),
              limit: f.usd(backends.data.items[0].spend_limit_usd),
            })}
          </p>
        )}
      </div>
    </div>
  );
}

function BudgetCard({ ai, today, month, unavailable }: {
  ai: ProjectAi;
  today: AiUsageItem[];
  month: AiUsageItem[];
  unavailable: boolean;
}) {
  const t = useTranslations("ai.tab");
  const f = useAiFormat();
  const used = usageTotals(today);
  const monthCost = usageTotals(month).cost;
  const pct = ai.daily_token_budget > 0 ? Math.round((used.tokens / ai.daily_token_budget) * 100) : 0;
  return (
    <div className={cn(panel, "flex items-center gap-4")}>
      <RingGauge
        value={used.tokens}
        max={ai.daily_token_budget}
        label={t("budgetGauge")}
        valueText={t("budgetValue", { used: f.tokens(used.tokens), budget: f.tokens(ai.daily_token_budget) })}
        size={112}
      >
        <span>
          <span className="block text-xl font-semibold tabular-nums">{unavailable ? "?" : `${pct} %`}</span>
          <span className="text-[0.7rem] text-muted-foreground">{t("today")}</span>
        </span>
      </RingGauge>
      <div className="min-w-0 space-y-1.5">
        <h3 className="text-sm font-semibold">{t("budgetTitle")}</h3>
        <p className="text-sm tabular-nums">
          <span className="font-semibold">{f.tokens(used.tokens)}</span>
          <span className="text-muted-foreground"> / {f.tokens(ai.daily_token_budget)} {t("tokens")}</span>
        </p>
        <p className="text-sm tabular-nums">
          <span className="font-semibold">{f.eur(monthCost)}</span>
          <span className="text-muted-foreground"> {t("thisMonth")}</span>
        </p>
        {unavailable ? (
          <p role="status" className="text-xs text-amber-800 dark:text-amber-300">{t("usageUnavailable")}</p>
        ) : (
          <p className="text-xs text-muted-foreground">{t("alertAt", { pct: ALERT_PCT })}</p>
        )}
      </div>
    </div>
  );
}

function GrantDialog({ open, onOpenChange, candidates, onGranted }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidates: string[];
  onGranted: () => void;
}) {
  const t = useTranslations("ai.grant");
  const { projectName } = useProjectContext();
  const [app, setApp] = useState("");
  const [budget, setBudget] = useState(50_000);
  const [busy, setBusy] = useState(false);
  const appId = useId();
  const budgetId = useId();
  const chosen = app || candidates[0] || "";

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!chosen) return;
    setBusy(true);
    try {
      const result = await grantAppAi(projectName, chosen, { enabled: true, daily_token_budget: budget });
      toast.success(t("done", { client: result.client_id }));
      onGranted();
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t("title")}</DialogTitle>
            <DialogDescription>{t("body")}</DialogDescription>
          </DialogHeader>
          {candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor={appId}>{t("app")}</Label>
                <select
                  id={appId}
                  value={chosen}
                  onChange={(e) => setApp(e.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm dark:bg-input/30"
                >
                  {candidates.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={budgetId}>{t("budget")}</Label>
                <Input
                  id={budgetId}
                  type="number"
                  min={1000}
                  step={1000}
                  value={budget}
                  onChange={(e) => setBudget(Number(e.target.value))}
                />
              </div>
            </>
          )}
          <DialogFooter>
            <Button type="submit" disabled={!chosen || busy}>{t("submit")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AppsTable({ ai, usage, onGranted }: { ai: ProjectAi; usage: AiUsageItem[]; onGranted: () => void }) {
  const t = useTranslations("ai.tab");
  const f = useAiFormat();
  const { projectName, apps, can } = useProjectContext();
  const [granting, setGranting] = useState(false);
  const granted = ai.apps.filter((a) => a.enabled);
  const candidates = apps
    .filter((a) => a.status !== "deleted" && !granted.some((g) => g.app === a.name))
    .map((a) => a.name);
  return (
    <div className={cn(panel, "space-y-3")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{t("appsTitle")}</h3>
          <p className="text-xs text-muted-foreground">{t("appsHint")}</p>
        </div>
        {can("ai.configure") && ai.enabled && (
          <Button variant="outline" size="sm" onClick={() => setGranting(true)}>
            <Sparkles aria-hidden />
            {t("grant")}
          </Button>
        )}
      </div>
      {granted.length === 0 ? (
        <p className="rounded-lg border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
          {t("noApps")}
        </p>
      ) : (
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t("appsTitle")}>
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">{t("colApp")}</th>
                <th scope="col" className="py-2 pr-3 font-medium">{t("colClient")}</th>
                <th scope="col" className="w-1/3 py-2 pr-3 font-medium">{t("colTokens")}</th>
                <th scope="col" className="py-2 pr-3 font-medium">{t("colRefused")}</th>
                <th scope="col" className="py-2 text-right font-medium">{t("colCost")}</th>
              </tr>
            </thead>
            <tbody>
              {granted.map((a) => {
                const client = a.client_id ?? `app-${projectName}-${a.app}`;
                const u = usage.find((item) => item.azp === client);
                const tokens = u ? u.tokens_in + u.tokens_out : 0;
                return (
                  <tr key={a.app} className="border-b last:border-0">
                    <td className="py-2.5 pr-3 font-medium">{a.app}</td>
                    <td className="py-2.5 pr-3 font-mono text-xs text-muted-foreground">{client}</td>
                    <td className="py-2.5 pr-3">
                      <div className="flex items-center gap-2">
                        <BarGauge value={tokens} max={a.daily_token_budget} label={t("appBudget", { app: a.app })} />
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                          {f.tokens(tokens)} / {f.tokens(a.daily_token_budget)}
                        </span>
                      </div>
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums">{u?.refused_total ?? 0}</td>
                    <td className="py-2.5 text-right tabular-nums">{f.eur(u?.cost_eur ?? 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <GrantDialog open={granting} onOpenChange={setGranting} candidates={candidates} onGranted={onGranted} />
    </div>
  );
}

function HostedAgents({ enabled }: { enabled: boolean }) {
  const t = useTranslations("ai.tab");
  const { projectName, can } = useProjectContext();
  const agents = useCardData(`agents-${projectName}`, () => getProjectAgents(projectName), {
    intervalMs: 30_000,
  });
  const canCreate = can("agent.manage") && enabled;
  return (
    <section aria-labelledby="ai-agents" className="space-y-4">
      <SectionTitle
        id="ai-agents"
        icon={Bot}
        title={t("agentsTitle")}
        hint={t("agentsHint")}
        action={
          canCreate && (
            <Link href={projectHref(projectName, "ai/new")} className={buttonVariants({ size: "lg" })}>
              <Plus aria-hidden />
              {t("createAgent")}
            </Link>
          )
        }
      />
      {agents.loading && !agents.data ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-44 rounded-xl" />)}
        </div>
      ) : agents.error && !agents.data ? (
        <p className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="size-4" aria-hidden />
          {t("agentsError")}
        </p>
      ) : agents.data && agents.data.length > 0 ? (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {agents.data.map((agent) => (
            <li key={agent.name} className="contents">
              <AgentCard agent={agent} />
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-10 text-center">
          <span className="grid size-12 place-items-center rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 text-primary">
            <Bot className="size-6" aria-hidden />
          </span>
          <p className="font-medium">{t("noAgents")}</p>
          <p className="max-w-md text-sm text-muted-foreground">
            {enabled ? t("noAgentsBody") : t("enableFirst")}
          </p>
        </div>
      )}
    </section>
  );
}

export function ProjectAiTab() {
  const t = useTranslations("ai.tab");
  const { projectName, can } = useProjectContext();
  const ai = useCardData(`ai-${projectName}`, () => getProjectAi(projectName));
  const [override, setOverride] = useState<ProjectAi | null>(null);
  const today = useCardData(`ai-usage-1d-${projectName}`, () => getProjectAiUsage(projectName, "1d"), {
    intervalMs: 30_000,
  });
  const month = useCardData(`ai-usage-30d-${projectName}`, () => getProjectAiUsage(projectName, "30d"));
  const current = override ?? ai.data;

  return (
    <div className="space-y-10">
      <section aria-labelledby="ai-access" className="space-y-4">
        <SectionTitle id="ai-access" icon={Sparkles} title={t("accessSection")} hint={t("accessHint")} />
        {ai.loading && !current ? (
          <div className="grid gap-4 md:grid-cols-3">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}
          </div>
        ) : !current ? (
          <p className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            <AlertCircle className="size-4" aria-hidden />
            {t("loadError")}
          </p>
        ) : (
          <>
            {current.degraded && current.degraded.length > 0 && (
              <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
                <CloudOff className="size-3.5 shrink-0" aria-hidden />
                {t("degraded")}
              </p>
            )}
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <AccessCard ai={current} onChange={setOverride} />
              <BudgetCard
                ai={current}
                today={today.data?.items ?? []}
                month={month.data?.items ?? []}
                unavailable={!today.loading && !today.data}
              />
              <ModelsCard ai={current} />
            </div>
            <AppsTable ai={current} usage={today.data?.items ?? []} onGranted={ai.retry} />
          </>
        )}
      </section>

      <HostedAgents enabled={current?.enabled ?? false} />

      <section aria-labelledby="ai-assistants" className="space-y-4">
        <SectionTitle id="ai-assistants" icon={Plug} title={t("assistantsTitle")} hint={t("assistantsHint")} />
        <ProjectAgentPanel project={projectName} canConfigure={can("agents.configure")} />
      </section>
    </div>
  );
}
