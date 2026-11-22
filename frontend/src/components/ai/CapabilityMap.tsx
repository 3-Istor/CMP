"use client";

import { Badge } from "@/components/ui/badge";
import {
  answersFromTools,
  RULE_OF_TWO,
  riskProfile,
  strongerIsolation,
  toolColumns,
  type RiskAnswers,
  type RuleOfTwoRisk,
} from "@/lib/ai/risk";
import { cn } from "@/lib/utils";
import type { AgentIsolation, AiModelId, AiTool } from "@/types/ai";
import { ArrowDown, ArrowRight, Bot, Globe, KeyRound, ShieldAlert, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";
import { IsolationBadge, RISK_META } from "./shared";

export interface CapabilityNode {
  name: string;
  model?: AiModelId;
  tools: string[];
  isolation?: AgentIsolation;
  /** Risk answers given by hand in the wizard; derived from the tools otherwise. */
  answers?: RiskAnswers;
}

const LAMP: Record<RuleOfTwoRisk, { icon: typeof Globe; on: string }> = {
  untrusted_content: { icon: ShieldAlert, on: "border-amber-500/50 bg-amber-500/15 text-amber-800 dark:text-amber-300" },
  private_data: { icon: KeyRound, on: "border-violet-500/50 bg-violet-500/15 text-violet-800 dark:text-violet-300" },
  external: { icon: Globe, on: "border-rose-500/50 bg-rose-500/15 text-rose-800 dark:text-rose-300" },
};

export function RiskLamps({ lit, compact = false }: { lit: RuleOfTwoRisk[]; compact?: boolean }) {
  const t = useTranslations("ai.ruleOfTwo");
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={t("lampsLabel")}>
      {RULE_OF_TWO.map((risk) => {
        const on = lit.includes(risk);
        const { icon: Icon, on: onClass } = LAMP[risk];
        return (
          <li
            key={`${risk}-${on}`}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors duration-300",
              on ? cn(onClass, "ai-lamp-on") : "border-dashed text-muted-foreground",
              compact && "px-2 py-0.5",
            )}
          >
            <Icon className="size-3.5" aria-hidden />
            {t(risk)}
            <span className="sr-only">{on ? t("on") : t("off")}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Verdict({ count }: { count: number }) {
  const t = useTranslations("ai.ruleOfTwo");
  return (
    <Badge
      variant="secondary"
      className={cn(
        "tabular-nums transition-colors",
        count === 3
          ? "bg-destructive/10 text-destructive"
          : "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
      )}
    >
      {t("verdict", { count })}
    </Badge>
  );
}

/** Vertical while the lane is narrow, horizontal once it has room. */
function Flow({ active }: { active: boolean }) {
  return (
    <div
      aria-hidden
      className={cn(
        "h-6 w-0.5 shrink-0 self-center transition-colors duration-500 @xl:h-0.5 @xl:w-auto @xl:min-w-6 @xl:flex-1",
        active ? "ai-flow-y text-primary/70 @xl:ai-flow-x" : "bg-border",
      )}
    />
  );
}

function ToolChip({ tool }: { tool: AiTool }) {
  return (
    <li className="flex min-w-0 items-center gap-2 rounded-lg border bg-background/60 px-2.5 py-1.5 text-xs animate-in fade-in-0 slide-in-from-left-1 duration-300">
      <Wrench className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="truncate font-mono">{tool.name}</span>
      <span className="ml-auto flex shrink-0 gap-0.5">
        {tool.badges.map((risk) => {
          const { icon: Icon, className } = RISK_META[risk];
          return (
            <span key={risk} className={cn("grid size-5 place-items-center rounded-full", className)}>
              <Icon className="size-3" aria-hidden />
            </span>
          );
        })}
      </span>
    </li>
  );
}

function Column({ title, tools, empty, children }: {
  title: string;
  tools: AiTool[];
  empty: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="min-w-0 flex-1 space-y-2">
      <p className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">{title}</p>
      {tools.length || children ? (
        <ul className="space-y-1.5">
          {children}
          {tools.map((tool) => (
            <ToolChip key={tool.name} tool={tool} />
          ))}
        </ul>
      ) : (
        <p className="rounded-lg border border-dashed px-2.5 py-2 text-xs text-muted-foreground">{empty}</p>
      )}
    </div>
  );
}

function Lane({ node, registry, index, receivesFrom }: {
  node: CapabilityNode;
  registry: AiTool[];
  index?: number;
  receivesFrom?: string;
}) {
  const t = useTranslations("ai.ruleOfTwo");
  const answers = node.answers ?? answersFromTools(node.tools, registry);
  const profile = riskProfile(answers);
  const { sources, actions } = toolColumns(node.tools, registry);
  const isolation = strongerIsolation(node.isolation ?? "runc", profile.isolation);
  return (
    <section
      aria-label={t("laneLabel", { name: node.name })}
      className={cn(
        "@container flex-1 space-y-4 rounded-xl border bg-card p-4 transition-shadow duration-500",
        profile.blocked && "border-destructive/50 shadow-[0_0_0_3px] shadow-destructive/15",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="flex items-center gap-2 text-sm font-semibold">
          {index !== undefined && (
            <span className="grid size-5 place-items-center rounded-full bg-primary/10 text-[0.7rem] text-primary">
              {index}
            </span>
          )}
          <span className="font-mono">{node.name || t("unnamed")}</span>
        </h4>
        <Verdict count={profile.lit.length} />
      </div>
      <RiskLamps lit={profile.lit} />
      <div className="flex flex-col items-stretch gap-3 @xl:flex-row">
        <Column title={t("reads")} tools={sources} empty={t("noSource")}>
          {receivesFrom && (
            <li className="flex min-w-0 items-center gap-2 rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-1.5 text-xs">
              <ArrowRight className="size-3.5 shrink-0 text-primary" aria-hidden />
              <span className="truncate">{t("fromReader", { name: receivesFrom })}</span>
            </li>
          )}
        </Column>
        <Flow active={sources.length > 0 || Boolean(receivesFrom)} />
        <div className="flex shrink-0 flex-col items-center justify-center gap-1.5 self-center rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-center">
          <span className="grid size-9 place-items-center rounded-full bg-primary text-primary-foreground shadow-[0_0_24px] shadow-primary/40">
            <Bot className="size-5" aria-hidden />
          </span>
          {node.model && <span className="font-mono text-[0.7rem] text-muted-foreground">{node.model}</span>}
          <IsolationBadge isolation={isolation} />
        </div>
        <Flow active={actions.length > 0} />
        <Column title={t("acts")} tools={actions} empty={t("noAction")} />
      </div>
      {profile.blocked && (
        <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {t("blocked")}
        </p>
      )}
    </section>
  );
}

/**
 * Read-only map of what an agent reads, what it can do and the three Rule of
 * Two risks. Two nodes draw a reader to actor chain over A2A.
 */
export function CapabilityMap({
  nodes,
  registry,
  wide = false,
}: {
  nodes: CapabilityNode[];
  registry: AiTool[];
  /** Lay a chain out left to right on extra-large screens. */
  wide?: boolean;
}) {
  const t = useTranslations("ai.ruleOfTwo");
  if (nodes.length === 1) return <Lane node={nodes[0]} registry={registry} />;
  return (
    <div className={cn("flex flex-col items-stretch gap-2", wide && "xl:flex-row")}>
      {nodes.map((node, i) => (
        <div key={node.name || i} className="contents">
          {i > 0 && (
            <div className={cn("flex shrink-0 items-center justify-center gap-2 py-1 text-xs text-muted-foreground", wide && "xl:flex-col xl:px-1")}>
              <ArrowDown className={cn("size-4 text-primary", wide && "xl:hidden")} aria-hidden />
              {wide && <ArrowRight className="hidden size-4 text-primary xl:block" aria-hidden />}
              <span className="rounded-full border bg-background px-2 py-0.5 font-medium">{t("a2a")}</span>
            </div>
          )}
          <Lane node={node} registry={registry} index={i + 1} receivesFrom={i > 0 ? nodes[i - 1].name : undefined} />
        </div>
      ))}
    </div>
  );
}
