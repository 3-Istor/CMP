"use client";

import { projectHref } from "@/components/layout/projectSections";
import { getAgentUsage } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { Agent } from "@/types/ai";
import { ArrowUpRight, Bot } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { AgentStatusBadge, BarGauge, IsolationBadge, Sparkline, useAiFormat } from "./shared";

export function agentHref(project: string, agent: string, tab = "") {
  const base = `${projectHref(project, "ai")}/agents/${encodeURIComponent(agent)}`;
  return tab ? `${base}/${tab}` : base;
}

export function AgentCard({ agent }: { agent: Agent }) {
  const t = useTranslations("ai.agentCard");
  const tTrigger = useTranslations("ai.trigger");
  const f = useAiFormat();
  const suspended = agent.status === "suspended";
  const tokens = agent.usage_today?.tokens ?? 0;
  const usage = useCardData(`agent-usage-1d-${agent.project}-${agent.name}`, () =>
    getAgentUsage(agent.project, agent.name, "1d"),
  );
  return (
    <Link
      href={agentHref(agent.project, agent.name)}
      className={cn(
        "group relative flex flex-col gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition-all",
        "hover:-translate-y-0.5 hover:shadow-lg hover:shadow-primary/5 hover:ring-primary/40",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        suspended && "bg-muted/40",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "grid size-9 shrink-0 place-items-center rounded-lg",
            suspended ? "bg-muted text-muted-foreground" : "bg-primary/10 text-primary",
          )}
        >
          <Bot className="size-[18px]" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-mono text-sm font-semibold">{agent.name}</span>
            <ArrowUpRight
              className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
              aria-hidden
            />
          </div>
          <p className="line-clamp-2 text-xs text-muted-foreground">{agent.description}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <AgentStatusBadge status={agent.status} />
        <IsolationBadge isolation={agent.isolation} />
        <span className="text-xs text-muted-foreground">
          {tTrigger(agent.trigger.type)} · <span className="font-mono">{agent.model}</span>
        </span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-xs text-muted-foreground">
            <span className="text-base font-semibold tabular-nums text-foreground">
              {f.tokens(tokens)}
            </span>{" "}
            {t("tokensToday", { budget: f.tokens(agent.daily_token_budget) })}
          </p>
          <BarGauge
            value={tokens}
            max={agent.daily_token_budget}
            label={t("budgetLabel", { name: agent.name })}
          />
          <p className="text-xs tabular-nums text-muted-foreground">
            {t("costToday", { cost: f.eur(agent.usage_today?.cost_eur ?? 0) })}
          </p>
        </div>
        <Sparkline points={usage.data?.series.map((p) => p.tokens) ?? []} tone={suspended ? "muted" : "primary"} />
      </div>
    </Link>
  );
}
