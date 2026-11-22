"use client";

import { projectHref } from "@/components/layout/projectSections";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  createAgent,
  draftAgent,
  getAgentTemplates,
  getProjectAi,
  getProjectAiTools,
} from "@/lib/api";
import { answersFromTools, riskProfile, strongerIsolation, type RiskAnswers } from "@/lib/ai/risk";
import { agentValuesYaml } from "@/lib/ai/yaml";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type {
  AgentBlock,
  AgentDraft,
  AgentEffort,
  AgentIsolation,
  AgentTemplate,
  AgentTemplateId,
  AgentTriggerType,
  AiTool,
  CreateAgentRequest,
  CreateAgentResult,
  ToolRisk,
} from "@/types/ai";
import {
  ArrowLeft,
  ArrowRight,
  BookOpenText,
  Bot,
  ChartColumn,
  Check,
  ExternalLink,
  GitPullRequest,
  KeyRound,
  Loader2,
  Network,
  Sparkles,
  Stethoscope,
  Wallet,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { CapabilityMap, type CapabilityNode } from "./CapabilityMap";
import { agentHref } from "./AgentCard";
import { IsolationBadge, RiskBadge, useAiFormat } from "./shared";

// Lucide names used by app-templates agents/*.yaml `icon`.
const TEMPLATE_ICON: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  "book-open-text": BookOpenText,
  "chart-column": ChartColumn,
  bot: Bot,
  stethoscope: Stethoscope,
  "git-pull-request": GitPullRequest,
};

function templateBadges(template: AgentTemplate): ToolRisk[] {
  return [...new Set(template.suggested_tools.flatMap((tool) => tool.badges ?? []))];
}

const STEPS = ["start", "identity", "model", "tools", "risk", "summary"] as const;
type Step = (typeof STEPS)[number];

// Mirrors backend declaration.NAME.
const NAME_RE = /^[a-z]([a-z0-9-]{0,38}[a-z0-9])?$/;
const EFFORTS: AgentEffort[] = ["low", "medium", "high"];
const TRIGGERS: AgentTriggerType[] = ["chat", "event", "schedule", "a2a"];
const ISOLATIONS: AgentIsolation[] = ["runc", "gvisor", "kata"];

interface ActorState {
  instructions: string;
  tools: string[];
  budget: number;
}

interface WizardState {
  template: AgentTemplateId;
  name: string;
  repoApp: string;
  description: string;
  owner: string;
  model: string;
  effort: AgentEffort;
  maxSteps: number;
  trigger: AgentTriggerType;
  cron: string;
  instructions: string;
  tools: string[];
  budget: number;
  isolation: AgentIsolation;
  answers: RiskAnswers | null;
  actor: ActorState | null;
}

const EMPTY: WizardState = {
  template: "assistant",
  name: "",
  repoApp: "",
  description: "",
  owner: "",
  model: "claude-haiku-5-5",
  effort: "low",
  maxSteps: 8,
  trigger: "chat",
  cron: "0 8 * * *",
  instructions: "",
  tools: [],
  budget: 300_000,
  isolation: "runc",
  answers: null,
  actor: null,
};

function fromBlock(
  base: WizardState,
  template: AgentTemplateId,
  block: AgentBlock,
  actor: AgentBlock | null,
): WizardState {
  return {
    ...EMPTY,
    owner: base.owner,
    repoApp: base.repoApp,
    template,
    description: block.description ?? "",
    model: block.model ?? EMPTY.model,
    effort: block.effort ?? EMPTY.effort,
    maxSteps: block.maxSteps ?? EMPTY.maxSteps,
    trigger: block.trigger?.type ?? EMPTY.trigger,
    cron: block.trigger?.cron ?? EMPTY.cron,
    instructions: block.instructions ?? "",
    tools: block.tools ?? [],
    actor: actor ? { instructions: actor.instructions ?? "", tools: actor.tools ?? [], budget: 50_000 } : null,
  };
}

/** app-bot creates <name>-reader and <name>-actor (backend service.create). */
export function createdNames(state: Pick<WizardState, "name" | "actor">): string[] {
  return state.actor ? [`${state.name}-reader`, `${state.name}-actor`] : [state.name];
}

export function buildRequest(state: WizardState, isolation: AgentIsolation, minimum: AgentIsolation): CreateAgentRequest {
  return {
    template: state.template,
    name: state.name,
    repo_app: state.repoApp,
    description: state.description,
    owner: state.owner.trim() || null,
    model: state.model,
    effort: state.effort,
    max_steps: state.maxSteps,
    trigger: state.trigger === "schedule" ? { type: state.trigger, cron: state.cron } : { type: state.trigger },
    instructions: state.instructions,
    tools: state.tools,
    // null lets the backend derive the minimum from the tools; send only a stronger choice.
    isolation: isolation === minimum ? null : isolation,
    callers: [],
    calls: [],
    daily_token_budget: state.budget,
    output: null,
    ...(state.actor
      ? { actor: { instructions: state.actor.instructions, tools: state.actor.tools, daily_token_budget: state.actor.budget } }
      : {}),
  };
}

function valuesPreview(state: WizardState, isolation: AgentIsolation): string {
  const [first, second] = createdNames(state);
  const reader: AgentBlock = {
    template: state.template,
    description: state.description,
    model: state.model,
    effort: state.effort,
    maxSteps: state.maxSteps,
    trigger: state.trigger === "schedule" ? { type: state.trigger, cron: state.cron } : { type: state.trigger },
    instructions: state.instructions,
    tools: state.tools,
    ...(second ? { calls: [second] } : {}),
  };
  let yaml = `# deploy/values-${first}.yaml (isolation: ${isolation})\n${agentValuesYaml(reader)}`;
  if (state.actor && second) {
    yaml += `\n\n# deploy/values-${second}.yaml\n${agentValuesYaml({
      template: state.template,
      description: state.description,
      model: state.model,
      effort: "low",
      maxSteps: 4,
      trigger: { type: "a2a" },
      instructions: state.actor.instructions,
      tools: state.actor.tools,
    })}`;
  }
  return yaml;
}

function StepHeader({ step, maxReached, onGo }: { step: number; maxReached: number; onGo: (i: number) => void }) {
  const t = useTranslations("ai.wizard.steps");
  return (
    <nav aria-label={t("label")} tabIndex={0} className="overflow-x-auto pb-2 outline-none [scrollbar-width:thin] focus-visible:ring-2 focus-visible:ring-ring">
      <ol className="flex min-w-max items-center gap-2 px-1">
        {STEPS.map((key, i) => {
          const done = i < step;
          const active = i === step;
          const reachable = i <= maxReached && i !== step;
          return (
            <li key={key} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onGo(i)}
                disabled={!reachable}
                aria-current={active ? "step" : undefined}
                className="flex items-center gap-2 rounded-full pr-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
              >
                <span
                  className={cn(
                    "flex size-8 items-center justify-center rounded-full text-xs font-bold transition-all duration-300",
                    done
                      ? "bg-primary text-primary-foreground"
                      : active
                        ? "bg-primary/80 text-primary-foreground ring-4 ring-primary/20"
                        : "bg-muted text-muted-foreground",
                  )}
                >
                  {done ? <Check className="size-4" aria-hidden /> : i + 1}
                </span>
                <span
                  className={cn(
                    "whitespace-nowrap text-sm transition-colors",
                    active ? "font-semibold text-foreground" : "text-muted-foreground",
                  )}
                >
                  {t(key)}
                  {done && <span className="sr-only"> ({t("done")})</span>}
                </span>
              </button>
              {i < STEPS.length - 1 && (
                <span aria-hidden className={cn("h-px w-8 transition-colors duration-300", done ? "bg-primary" : "bg-muted")} />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function Field({ label, hint, children, id }: { label: string; hint?: string; children: React.ReactNode; id: string }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p id={`${id}-hint`} className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

const textareaClass =
  "w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

function ChoiceGroup<T extends string>({ label, options, value, onChange, render }: {
  label: string;
  options: T[];
  value: T;
  onChange: (value: T) => void;
  render: (option: T) => React.ReactNode;
}) {
  const name = useId();
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{label}</legend>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {options.map((option) => (
          <label
            key={option}
            className={cn(
              "flex cursor-pointer items-start gap-2.5 rounded-xl border p-3 text-sm transition-all",
              "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
              value === option ? "border-primary bg-primary/5 shadow-sm" : "hover:bg-muted/50",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option}
              checked={value === option}
              onChange={() => onChange(option)}
              className="mt-0.5 accent-[var(--primary)]"
            />
            <span className="min-w-0">{render(option)}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function StartStep({ templates, onDraft, onTemplate }: {
  templates: AgentTemplate[] | null;
  onDraft: (draft: AgentDraft) => void;
  onTemplate: (template: AgentTemplate) => void;
}) {
  const t = useTranslations("ai.wizard.start");
  const tTpl = useTranslations("ai.templates");
  const { projectName } = useProjectContext();
  const [sentence, setSentence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  const draft = async (event: React.FormEvent) => {
    event.preventDefault();
    if (sentence.trim().length < 8) return;
    setBusy(true);
    setError(null);
    try {
      onDraft(await draftAgent(projectName, sentence.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-8">
      <form
        onSubmit={draft}
        className="relative overflow-hidden rounded-2xl border border-primary/30 bg-gradient-to-br from-primary/10 via-card to-card p-5 shadow-sm"
      >
        <Label htmlFor={id} className="mb-3 flex items-center gap-2 text-base font-semibold">
          <Wand2 className="size-5 text-primary" aria-hidden />
          {t("describe")}
        </Label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <textarea
            id={id}
            rows={2}
            value={sentence}
            onChange={(e) => setSentence(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={t("placeholder")}
            aria-describedby={`${id}-hint`}
            className={cn(textareaClass, "resize-none bg-background text-base")}
          />
          <Button type="submit" size="lg" disabled={busy || sentence.trim().length < 8} className="sm:self-stretch sm:px-5">
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
            {busy ? t("drafting") : t("draft")}
          </Button>
        </div>
        <p id={`${id}-hint`} className="mt-2 text-xs text-muted-foreground">{t("hint")}</p>
        {error && <p role="alert" className="mt-2 text-sm text-destructive">{t("draftError")}</p>}
      </form>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-muted-foreground">{t("orTemplate")}</h3>
        {!templates ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
          </div>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((template) => {
              const Icon = TEMPLATE_ICON[template.icon] ?? Bot;
              const badges = templateBadges(template);
              return (
                <li key={template.id}>
                  <button
                    type="button"
                    onClick={() => onTemplate(template)}
                    className="group flex h-full w-full flex-col gap-2 rounded-xl bg-card p-4 text-left ring-1 ring-foreground/10 transition-all hover:-translate-y-0.5 hover:shadow-lg hover:ring-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="flex items-center gap-2.5">
                      <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                        <Icon className="size-[18px]" aria-hidden />
                      </span>
                      <span className="font-semibold">{template.title}</span>
                    </span>
                    <span className="text-sm text-muted-foreground">{template.description}</span>
                    {(badges.length > 0 || template.pair) && (
                      <span className="mt-auto flex flex-wrap gap-1 pt-1">
                        {template.pair && <Badge variant="secondary">{tTpl("pair")}</Badge>}
                        {badges.map((risk) => <RiskBadge key={risk} risk={risk} />)}
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function ToolList({ tools, selected, onToggle, label }: {
  tools: AiTool[];
  selected: string[];
  onToggle: (name: string) => void;
  label: string;
}) {
  const t = useTranslations("ai.wizard.tools");
  const sources = [...new Set(tools.map((tool) => tool.source))];
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-semibold">{label}</legend>
      {sources.map((source) => (
        <div key={source} className="space-y-1.5">
          <p className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
            {source === "platform" ? t("platform") : source}
          </p>
          <ul className="space-y-1.5">
            {tools.filter((tool) => tool.source === source).map((tool) => {
              const checked = selected.includes(tool.name);
              return (
                <li key={tool.name}>
                  <label
                    className={cn(
                      "flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border p-2.5 text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
                      checked ? "border-primary/50 bg-primary/5" : "hover:bg-muted/50",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggle(tool.name)}
                      className="size-4 accent-[var(--primary)]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono text-xs font-semibold">{tool.name}</span>
                      <span className="block text-xs text-muted-foreground">{tool.description}</span>
                    </span>
                    <span className="flex flex-wrap gap-1">
                      {tool.badges.map((risk) => <RiskBadge key={risk} risk={risk} />)}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </fieldset>
  );
}

function RiskQuestion({ label, checked, onChange, derived }: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  derived: boolean;
}) {
  const t = useTranslations("ai.wizard.risk");
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
      <Label htmlFor={id} className="flex-1 font-normal">
        {label}
        {derived && <span className="ml-2 text-xs text-muted-foreground">{t("fromTools")}</span>}
      </Label>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function Chips({ values }: { values: string[] }) {
  return (
    <span className="mt-1 flex flex-wrap gap-1.5">
      {values.map((v) => (
        <code key={v} className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">{v}</code>
      ))}
    </span>
  );
}

function Summary({ state, isolation, project }: { state: WizardState; isolation: AgentIsolation; project: string }) {
  const t = useTranslations("ai.wizard.summary");
  const f = useAiFormat();
  const names = createdNames(state);
  const budgets = state.actor ? [state.budget, state.actor.budget] : [state.budget];
  const items: { icon: LucideIcon; title: string; detail: React.ReactNode }[] = [
    {
      icon: GitPullRequest,
      title: t("pr"),
      detail: (
        <>
          <span className="block">{t("prBody", { app: state.repoApp })}</span>
          <Chips values={names.map((n) => `deploy/values-${n}.yaml`)} />
        </>
      ),
    },
    { icon: KeyRound, title: t("client"), detail: <Chips values={names.map((n) => `agent-${project}-${n}`)} /> },
    { icon: Network, title: t("namespace"), detail: <Chips values={names.map((n) => `${project}-${n}`)} /> },
    {
      icon: Wallet,
      title: t("budget"),
      detail: names.map((n, i) => (
        <span key={n} className="block tabular-nums">
          <span className="font-mono text-xs text-foreground">{n}</span> {t("perDay", { tokens: f.tokens(budgets[i]) })}
        </span>
      )),
    },
  ];
  return (
    <div className="space-y-5">
      <ul className="grid gap-3 sm:grid-cols-2">
        {items.map(({ icon: Icon, title, detail }) => (
          <li key={title} className="flex gap-3 rounded-xl border bg-card p-3.5 animate-in fade-in-0 slide-in-from-bottom-1 duration-300">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
              <Icon className="size-4" aria-hidden />
            </span>
            <div className="min-w-0 space-y-0.5">
              <p className="text-sm font-semibold">{title}</p>
              <div className="break-words text-sm text-muted-foreground">{detail}</div>
            </div>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">{t("rights")}</p>
      <details className="group rounded-xl border bg-muted/30">
        <summary className="cursor-pointer select-none px-4 py-2.5 text-sm font-medium">{t("yaml")}</summary>
        <pre tabIndex={0} className="overflow-x-auto border-t px-4 py-3 font-mono text-xs leading-relaxed">
          {valuesPreview(state, isolation)}
        </pre>
      </details>
    </div>
  );
}

function Created({ result, project }: { result: CreateAgentResult; project: string }) {
  const t = useTranslations("ai.wizard.created");
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <div className="mx-auto max-w-xl space-y-5 py-10 text-center animate-in fade-in-0 zoom-in-95 duration-500">
      <span className="mx-auto grid size-16 place-items-center rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
        <Check className="size-8" aria-hidden />
      </span>
      <h2 ref={ref} tabIndex={-1} className="text-xl font-semibold outline-none">{t("title")}</h2>
      <p className="text-sm text-muted-foreground">{t("body")}</p>
      <div className="flex flex-wrap justify-center gap-2">
        {result.pull_request && (
          <a href={result.pull_request.url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "lg" })}>
            <GitPullRequest aria-hidden />
            {t("pr", { number: result.pull_request.number })}
            <ExternalLink aria-hidden />
          </a>
        )}
        <Link href={agentHref(project, result.agents[0]?.name ?? "")} className={buttonVariants({ size: "lg" })}>
          {t("open")}
          <ArrowRight aria-hidden />
        </Link>
      </div>
    </div>
  );
}

export function CreateAgentWizard() {
  const t = useTranslations("ai.wizard");
  const tTrigger = useTranslations("ai.trigger");
  const tIso = useTranslations("ai.isolation");
  const { projectName, apps } = useProjectContext();
  const [step, setStep] = useState(0);
  const [maxReached, setMaxReached] = useState(0);
  const [state, setState] = useState<WizardState>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<CreateAgentResult | null>(null);
  const [touchedName, setTouchedName] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);
  const ids = { name: useId(), desc: useId(), owner: useId(), instr: useId(), steps: useId(), cron: useId(), budget: useId(), actorInstr: useId(), actorBudget: useId(), repo: useId() };

  const templates = useCardData("ai-templates", getAgentTemplates);
  const tools = useCardData(`ai-tools-${projectName}`, () => getProjectAiTools(projectName));
  const ai = useCardData(`ai-${projectName}`, () => getProjectAi(projectName));
  const registry = useMemo(() => tools.data ?? [], [tools.data]);
  const models = ai.data?.models?.length ? ai.data.models : ["claude-haiku-5-5", "claude-sonnet-5-5"];

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  const set = <K extends keyof WizardState>(key: K, value: WizardState[K]) =>
    setState((s) => ({ ...s, [key]: value }));
  const setActor = <K extends keyof ActorState>(key: K, value: ActorState[K]) =>
    setState((s) => (s.actor ? { ...s, actor: { ...s.actor, [key]: value } } : s));

  const derived = answersFromTools(state.tools, registry);
  const answers = state.answers ?? derived;
  const profile = riskProfile(answers);
  const isolation = strongerIsolation(state.isolation, profile.isolation);

  const names = createdNames(state);
  const nodes: CapabilityNode[] = [
    { name: names[0], model: state.model, tools: state.tools, isolation, answers },
    ...(state.actor ? [{ name: names[1], model: state.model, tools: state.actor.tools }] : []),
  ];

  const repoApps = apps.filter((a) => a.status !== "deleted").map((a) => a.name);
  const repoApp = state.repoApp || repoApps[0] || "";
  const nameValid = NAME_RE.test(state.name) && names.every((n) => projectName.length + 1 + n.length <= 63);
  const canNext: Record<Step, boolean> = {
    start: false,
    identity: nameValid && Boolean(repoApp) && state.description.trim().length > 0,
    model: state.maxSteps >= 1 && state.budget > 0,
    tools: true,
    risk: !profile.blocked,
    summary: !profile.blocked && nameValid,
  };

  const go = (i: number) => {
    setStep(i);
    setMaxReached((m) => Math.max(m, i));
  };

  const pickTemplate = (template: AgentTemplate) => {
    const pair = template.pair;
    setState((s) =>
      fromBlock(
        s,
        template.id,
        { ...template.defaults, ...pair?.reader },
        pair ? { ...template.defaults, trigger: { type: "a2a" }, tools: [], ...pair.actor } : null,
      ),
    );
    go(1);
  };
  const pickDraft = (draft: AgentDraft) => {
    setState((s) => ({ ...fromBlock(s, draft.template, draft.agent, draft.actor), name: draft.name ?? "" }));
    toast.success(t("start.drafted"));
    if (draft.name) setTouchedName(true);
    go(1);
  };

  const submit = async () => {
    setSubmitting(true);
    try {
      setResult(await createAgent(projectName, buildRequest({ ...state, repoApp }, isolation, profile.isolation)));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (result) return <Created result={result} project={projectName} />;

  const key = STEPS[step];
  const toggle = (list: string[], name: string) =>
    list.includes(name) ? list.filter((n) => n !== name) : [...list, name];

  return (
    <div className="space-y-6">
      <StepHeader step={step} maxReached={maxReached} onGo={go} />
      <div className={cn("grid gap-6", step > 0 && "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]")}>
        <section aria-labelledby={ids.steps} className="min-w-0 space-y-5">
          <h2 id={ids.steps} ref={headingRef} tabIndex={-1} className="text-lg font-semibold tracking-tight outline-none">
            {t(`titles.${key}`)}
          </h2>

          {key === "start" && (
            <StartStep templates={templates.data} onDraft={pickDraft} onTemplate={pickTemplate} />
          )}

          {key === "identity" && (
            <div className="space-y-4">
              <Field id={ids.name} label={t("identity.name")} hint={t("identity.nameHint")}>
                <Input
                  id={ids.name}
                  value={state.name}
                  onChange={(e) => set("name", e.target.value.toLowerCase())}
                  onBlur={() => setTouchedName(true)}
                  aria-invalid={touchedName && !nameValid}
                  aria-describedby={`${ids.name}-hint`}
                  className="font-mono"
                  autoComplete="off"
                />
              </Field>
              <Field id={ids.desc} label={t("identity.description")}>
                <Input id={ids.desc} value={state.description} onChange={(e) => set("description", e.target.value)} />
              </Field>
              <Field id={ids.owner} label={t("identity.owner")}>
                <Input
                  id={ids.owner}
                  value={state.owner}
                  placeholder={t("identity.ownerPlaceholder")}
                  onChange={(e) => set("owner", e.target.value)}
                />
              </Field>
              <Field id={ids.instr} label={t("identity.instructions")}>
                <textarea
                  id={ids.instr}
                  rows={5}
                  value={state.instructions}
                  onChange={(e) => set("instructions", e.target.value)}
                  className={textareaClass}
                />
              </Field>
              <Field id={ids.repo} label={t("identity.repoApp")} hint={t("identity.repoAppHint")}>
                <select
                  id={ids.repo}
                  value={repoApp}
                  onChange={(e) => set("repoApp", e.target.value)}
                  aria-describedby={`${ids.repo}-hint`}
                  className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm dark:bg-input/30"
                >
                  {repoApps.length === 0 && <option value="">{t("identity.noApp")}</option>}
                  {repoApps.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </Field>
              {state.actor && (
                <div className="space-y-3 rounded-xl border border-dashed p-4">
                  <p className="text-sm font-semibold">{t("identity.actorTitle")}</p>
                  <p className="text-xs text-muted-foreground">{t("identity.actorHint")}</p>
                  <Field id={ids.actorInstr} label={t("identity.actorInstructions")}>
                    <textarea
                      id={ids.actorInstr}
                      rows={3}
                      value={state.actor.instructions}
                      onChange={(e) => setActor("instructions", e.target.value)}
                      className={textareaClass}
                    />
                  </Field>
                </div>
              )}
            </div>
          )}

          {key === "model" && (
            <div className="space-y-5">
              <ChoiceGroup
                label={t("model.model")}
                options={models}
                value={state.model}
                onChange={(v) => set("model", v)}
                render={(m) => (
                  <>
                    <span className="block font-mono text-xs font-semibold">{m}</span>
                    <span className="block text-xs text-muted-foreground">
                      {m.includes("sonnet") ? t("model.sonnet") : t("model.haiku")}
                    </span>
                  </>
                )}
              />
              <ChoiceGroup
                label={t("model.effort")}
                options={EFFORTS}
                value={state.effort}
                onChange={(v) => set("effort", v)}
                render={(e) => (
                  <>
                    <span className="block font-semibold">{t(`model.efforts.${e}`)}</span>
                    <span className="block text-xs text-muted-foreground">{t(`model.effortHints.${e}`)}</span>
                  </>
                )}
              />
              <ChoiceGroup
                label={t("model.trigger")}
                options={TRIGGERS}
                value={state.trigger}
                onChange={(v) => set("trigger", v)}
                render={(tr) => (
                  <>
                    <span className="block font-semibold">{tTrigger(tr)}</span>
                    <span className="block text-xs text-muted-foreground">{tTrigger(`${tr}Hint`)}</span>
                  </>
                )}
              />
              <div className="grid gap-4 sm:grid-cols-3">
                {state.trigger === "schedule" && (
                  <Field id={ids.cron} label={t("model.cron")}>
                    <Input id={ids.cron} value={state.cron} onChange={(e) => set("cron", e.target.value)} className="font-mono" />
                  </Field>
                )}
                <Field id={ids.steps} label={t("model.maxSteps")}>
                  <Input id={ids.steps} type="number" min={1} max={50} value={state.maxSteps} onChange={(e) => set("maxSteps", Number(e.target.value))} />
                </Field>
                <Field id={ids.budget} label={t("model.budget")}>
                  <Input id={ids.budget} type="number" min={1000} step={10000} value={state.budget} onChange={(e) => set("budget", Number(e.target.value))} />
                </Field>
                {state.actor && (
                  <Field id={ids.actorBudget} label={t("model.actorBudget")}>
                    <Input id={ids.actorBudget} type="number" min={1000} step={10000} value={state.actor.budget} onChange={(e) => setActor("budget", Number(e.target.value))} />
                  </Field>
                )}
              </div>
            </div>
          )}

          {key === "tools" &&
            (tools.loading && !tools.data ? (
              <Skeleton className="h-64 rounded-xl" />
            ) : tools.error && !tools.data ? (
              <p role="alert" className="text-sm text-destructive">{t("tools.error")}</p>
            ) : (
              <div className="space-y-6">
                <p className="text-sm text-muted-foreground">{t("tools.hint")}</p>
                <ToolList
                  tools={registry}
                  selected={state.tools}
                  onToggle={(name) => {
                    set("tools", toggle(state.tools, name));
                    set("answers", null);
                  }}
                  label={state.actor ? t("tools.reader", { name: names[0] }) : t("tools.label")}
                />
                {state.actor && (
                  <ToolList
                    tools={registry}
                    selected={state.actor.tools}
                    onToggle={(name) => setActor("tools", toggle(state.actor!.tools, name))}
                    label={t("tools.actor", { name: names[1] })}
                  />
                )}
              </div>
            ))}

          {key === "risk" && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">{t("risk.hint")}</p>
              {(
                [
                  ["executesCode", "code"],
                  ["untrustedInput", "untrusted"],
                  ["privateData", "private"],
                  ["external", "external"],
                ] as const
              ).map(([field, label]) => (
                <RiskQuestion
                  key={field}
                  label={t(`risk.questions.${label}`)}
                  checked={answers[field]}
                  derived={state.answers === null && derived[field]}
                  onChange={(value) => set("answers", { ...answers, [field]: value })}
                />
              ))}
              <ChoiceGroup
                label={t("risk.isolation")}
                options={ISOLATIONS}
                value={isolation}
                onChange={(v) => set("isolation", v)}
                render={(iso) => (
                  <>
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold">
                      {tIso(iso)}
                      {iso === profile.isolation && <Badge variant="secondary">{t("risk.minimum")}</Badge>}
                    </span>
                    <span className="block text-xs text-muted-foreground">{tIso(`${iso}Hint`)}</span>
                  </>
                )}
              />
              <div aria-live="polite" className="text-sm">
                {profile.blocked ? (
                  <p className="rounded-lg bg-destructive/10 px-3 py-2 text-destructive">{t("risk.split")}</p>
                ) : (
                  <p className="flex items-center gap-2 text-muted-foreground">
                    {t("risk.isolationResult")} <IsolationBadge isolation={isolation} />
                  </p>
                )}
              </div>
            </div>
          )}

          {key === "summary" && <Summary state={state} isolation={isolation} project={projectName} />}

          {step > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
              <Button variant="ghost" size="lg" onClick={() => go(step - 1)}>
                <ArrowLeft aria-hidden />
                {t("back")}
              </Button>
              {key === "summary" ? (
                <Button size="lg" onClick={() => void submit()} disabled={!canNext.summary || submitting}>
                  {submitting ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
                  {state.actor ? t("createPair") : t("create")}
                </Button>
              ) : (
                <Button size="lg" onClick={() => go(step + 1)} disabled={!canNext[key]}>
                  {t("next")}
                  <ArrowRight aria-hidden />
                </Button>
              )}
            </div>
          )}
        </section>

        {step > 0 && (
          <aside aria-label={t("mapLabel")} className="min-w-0 lg:sticky lg:top-24 lg:self-start">
            <div className="space-y-3 rounded-2xl border bg-gradient-to-b from-muted/40 to-transparent p-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">{t("mapTitle")}</h3>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-emerald-500" />
                  {t("live")}
                </span>
              </div>
              <CapabilityMap nodes={nodes} registry={registry} />
            </div>
          </aside>
        )}
      </div>
      <p className="sr-only" aria-live="polite">
        {t("stepAnnounce", { n: step + 1, total: STEPS.length, title: t(`titles.${key}`) })}
      </p>
      <Link href={projectHref(projectName, "ai")} className="sr-only focus:not-sr-only">
        {t("cancel")}
      </Link>
    </div>
  );
}
