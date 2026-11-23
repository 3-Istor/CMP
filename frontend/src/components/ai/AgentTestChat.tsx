"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchAgentArtifact, streamAgentTest } from "@/lib/api";
import { applyEvent, EMPTY_RUN, type Artifact, type RunState, type RunStep } from "@/lib/ai/testRun";
import { cn } from "@/lib/utils";
import {
  ArrowUp,
  Ban,
  Bot,
  ChevronRight,
  Coins,
  Cpu,
  ExternalLink,
  Image as ImageIcon,
  Loader2,
  RotateCcw,
  Square,
  Terminal,
  User,
  Wrench,
  Zap,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { Fragment, useEffect, useId, useRef, useState } from "react";
import { useAgentContext } from "./AgentFrame";
import { CodeBlock } from "./CodeBlock";
import { useAiFormat } from "./shared";

interface Turn {
  id: number;
  question: string;
  run: RunState;
}

function json(value: unknown) {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

/** Paragraphs, fenced code and inline code: enough for model answers, never raw HTML. */
function RichText({ text }: { text: string }) {
  const parts = text.split(/```(?:\w+)?\n?([\s\S]*?)(?:```|$)/g);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre key={i} className="my-2 overflow-x-auto rounded-lg bg-background/70 p-3 font-mono text-xs">{part}</pre>
        ) : (
          <Fragment key={i}>
            {part.split(/(`[^`\n]+`)/g).map((chunk, j) =>
              chunk.startsWith("`") && chunk.endsWith("`") && chunk.length > 2 ? (
                <code key={j} className="rounded bg-background/70 px-1 py-0.5 font-mono text-[0.85em]">
                  {chunk.slice(1, -1)}
                </code>
              ) : (
                chunk
              ),
            )}
          </Fragment>
        ),
      )}
    </>
  );
}

function useArtifactUrl(url: string): { src: string | null; failed: boolean } {
  const [state, setState] = useState<{ url: string; src: string | null; failed: boolean }>({ url, src: null, failed: false });
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    fetchAgentArtifact(url, controller.signal)
      .then((src) => {
        if (src.startsWith("blob:")) objectUrl = src;
        setState({ url, src, failed: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ url, src: null, failed: true });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return state.url === url ? state : { src: null, failed: false };
}

function ArtifactImage({ artifact }: { artifact: Artifact }) {
  const t = useTranslations("ai.test");
  const { src, failed } = useArtifactUrl(artifact.url);
  if (!artifact.mime.startsWith("image/") || failed) {
    return (
      <a href={src ?? artifact.url} download={artifact.name} className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline">
        <ExternalLink className="size-3.5" aria-hidden />
        {artifact.name}
      </a>
    );
  }
  return (
    <figure className="overflow-hidden rounded-lg border bg-white animate-in fade-in-0 zoom-in-95 duration-500">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- a blob URL of an authenticated artifact, nothing to optimise
        <img src={src} alt={t("artifactAlt", { name: artifact.name })} className="max-h-72 w-full object-contain" />
      ) : (
        <div className="grid h-40 place-items-center bg-muted/50" role="status" aria-label={t("loadingArtifact")}>
          <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
        </div>
      )}
      <figcaption className="flex items-center gap-1.5 border-t bg-card px-2.5 py-1.5 text-xs text-muted-foreground">
        <ImageIcon className="size-3.5" aria-hidden />
        <span className="truncate font-mono">{artifact.name}</span>
      </figcaption>
    </figure>
  );
}

function Collapsible({ label, children, defaultOpen = false }: { label: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} aria-hidden />
        {label}
      </button>
      <div id={id} hidden={!open} className="mt-1.5">
        {children}
      </div>
    </div>
  );
}

function StepIcon({ step, pending }: { step: RunStep; pending: boolean }) {
  const Icon =
    step.kind === "model" ? Cpu : step.kind === "tool" ? Wrench : step.kind === "sandbox" ? Terminal : step.kind === "refusal" ? Ban : ImageIcon;
  return (
    <span
      className={cn(
        "relative z-10 grid size-7 shrink-0 place-items-center rounded-full ring-4 ring-card",
        step.kind === "refusal"
          ? "bg-destructive text-white"
          : step.kind === "sandbox"
            ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
            : step.kind === "model"
              ? "bg-primary/15 text-primary"
              : "bg-muted text-foreground",
      )}
    >
      {pending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Icon className="size-3.5" aria-hidden />}
    </span>
  );
}

function StepCard({ step }: { step: RunStep }) {
  const t = useTranslations("ai.test.steps");
  const tReason = useTranslations("ai.refusal");
  const f = useAiFormat();
  const pending = (step.kind === "tool" && step.durationMs === undefined) || (step.kind === "model" && step.durationMs === null);
  return (
    <li className="relative flex gap-3 pb-4 animate-in fade-in-0 slide-in-from-left-2 duration-300 last:pb-0">
      <StepIcon step={step} pending={pending} />
      <div
        className={cn(
          "min-w-0 flex-1 space-y-2 rounded-lg border p-2.5",
          step.kind === "refusal" && "border-destructive/50 bg-destructive/10",
        )}
      >
        {step.kind === "model" && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="font-medium">{t("model")}</span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {step.durationMs === null ? t("running") : f.ms(step.durationMs)}
            </span>
            <span className="w-full text-xs text-muted-foreground">
              <span className="font-mono">{step.model}</span>
              {step.tokensIn !== null && step.tokensOut !== null && (
                <> · {t("tokens", { in: f.tokens(step.tokensIn), out: f.tokens(step.tokensOut) })}</>
              )}
            </span>
          </div>
        )}
        {step.kind === "tool" && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="min-w-0 truncate">
                <span className="font-medium">{t("tool")}</span>{" "}
                <span className="font-mono text-xs">{step.tool}</span>
                {step.server && <Badge variant="outline" className="ml-1.5">{t("serverTool")}</Badge>}
              </span>
              <span className="text-xs tabular-nums text-muted-foreground">
                {pending ? t("running") : f.ms(step.durationMs ?? 0)}
              </span>
            </div>
            <Collapsible label={t("args")}>
              <pre className="max-h-48 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-xs">{json(step.args)}</pre>
            </Collapsible>
            {!pending && (
              <Collapsible label={step.isError ? t("resultError") : t("result")}>
                <pre
                  className={cn(
                    "max-h-60 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-xs",
                    step.isError && "text-destructive",
                  )}
                >
                  {json(step.result)}
                </pre>
              </Collapsible>
            )}
          </>
        )}
        {step.kind === "sandbox" && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="flex items-center gap-2 font-medium">
                {t("sandbox")}
                <Badge
                  variant="secondary"
                  className={step.exitCode === 0 ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-destructive/10 text-destructive"}
                >
                  {step.exitCode === null ? t("timeout") : t("exit", { code: step.exitCode })}
                </Badge>
              </span>
              <span className="text-xs tabular-nums text-muted-foreground">{f.ms(step.durationMs)}</span>
            </div>
            <Collapsible label={t("code")} defaultOpen>
              <CodeBlock code={step.code} label={t("code")} />
            </Collapsible>
            {step.stdout && (
              <Collapsible label={t("stdout")} defaultOpen>
                <pre className="max-h-48 overflow-auto rounded-md bg-zinc-950 p-2 font-mono text-xs text-zinc-100">{step.stdout}</pre>
              </Collapsible>
            )}
            {step.stderr && (
              <Collapsible label={t("stderr")}>
                <pre className="max-h-48 overflow-auto rounded-md bg-zinc-950 p-2 font-mono text-xs text-red-300">{step.stderr}</pre>
              </Collapsible>
            )}
            {step.artifacts.map((artifact) => <ArtifactImage key={artifact.url} artifact={artifact} />)}
          </>
        )}
        {step.kind === "artifact" && <ArtifactImage artifact={step.artifact} />}
        {step.kind === "refusal" && (
          <div role="alert" className="space-y-0.5 text-sm text-destructive">
            <p className="font-semibold">
              {t("refusal", { reason: tReason(step.reason) })}
            </p>
            <p className="text-xs">{step.message}</p>
          </div>
        )}
      </div>
    </li>
  );
}

function Counter({ run, live }: { run: RunState; live: boolean }) {
  const t = useTranslations("ai.test");
  const f = useAiFormat();
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[0.7rem] tabular-nums text-muted-foreground">
      <span className="inline-flex items-center gap-1">
        <Zap className={cn("size-3", live && "animate-pulse text-primary")} aria-hidden />
        {t("tokensInOut", { in: f.tokens(run.tokensIn), out: f.tokens(run.tokensOut) })}
      </span>
      {run.costEur !== null && (
        <span className="inline-flex items-center gap-1">
          <Coins className="size-3" aria-hidden />
          {f.eur(run.costEur)}
        </span>
      )}
    </span>
  );
}

export function AgentTestChat() {
  const t = useTranslations("ai.test");
  const f = useAiFormat();
  const { project, agent } = useAgentContext();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [announce, setAnnounce] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stickRef = useRef(true);
  const inputId = useId();
  const suspended = agent.status === "suspended";

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const send = async (question: string) => {
    const text = question.trim();
    if (!text || streaming || suspended) return;
    const id = (turns.at(-1)?.id ?? 0) + 1;
    const history = turns.flatMap((turn) => [
      { role: "user" as const, content: turn.question },
      { role: "assistant" as const, content: turn.run.text },
    ]);
    setTurns((prev) => [...prev, { id, question: text, run: EMPTY_RUN }]);
    setSelected(id);
    setInput("");
    setStreaming(true);
    setAnnounce(t("thinking"));
    stickRef.current = true;
    const update = (fn: (run: RunState) => RunState) =>
      setTurns((prev) => prev.map((turn) => (turn.id === id ? { ...turn, run: fn(turn.run) } : turn)));
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamAgentTest(
        project,
        agent.name,
        { message: text, history },
        (event) => {
          const at = Date.now();
          update((run) => applyEvent(run, event, at));
        },
        controller.signal,
      );
      update((run) => ({ ...run, done: true }));
      setAnnounce(t("answered"));
    } catch (err) {
      const aborted = controller.signal.aborted;
      update((run) => ({ ...run, done: true, error: aborted ? t("stopped") : err instanceof Error ? err.message : String(err) }));
      setAnnounce(aborted ? t("stopped") : t("failed"));
    } finally {
      abortRef.current = null;
      setStreaming(false);
      inputRef.current?.focus();
    }
  };

  const current = turns.find((turn) => turn.id === selected) ?? turns.at(-1) ?? null;
  const session = turns.reduce(
    (acc, turn) => ({
      tokens: acc.tokens + turn.run.tokensIn + turn.run.tokensOut,
      cost: acc.cost + (turn.run.costEur ?? 0),
    }),
    { tokens: 0, cost: 0 },
  );
  const suggestions = [t("suggestions.capabilities"), t("suggestions.tools"), t("suggestions.limits")];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <section aria-labelledby="test-chat" className="flex h-[min(72vh,760px)] min-h-[480px] flex-col overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
          <div>
            <h2 id="test-chat" className="text-sm font-semibold">{t("title")}</h2>
            <p className="text-xs text-muted-foreground">{t("hint")}</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs tabular-nums text-muted-foreground" aria-live="polite">
              {t("session", { tokens: f.tokens(session.tokens), cost: f.eur(session.cost) })}
            </span>
            {turns.length > 0 && !streaming && (
              <Button variant="ghost" size="icon-sm" onClick={() => { setTurns([]); setSelected(null); }} aria-label={t("reset")} title={t("reset")}>
                <RotateCcw aria-hidden />
              </Button>
            )}
          </div>
        </div>

        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}
          role="log"
          aria-label={t("conversation")}
          aria-busy={streaming}
          className="flex-1 space-y-5 overflow-y-auto px-4 py-4"
        >
          {turns.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <span className="grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-primary/25 to-primary/5 text-primary shadow-[0_0_40px] shadow-primary/20">
                <Bot className="size-7" aria-hidden />
              </span>
              <div>
                <p className="font-medium">{t("emptyTitle", { name: agent.name })}</p>
                <p className="text-sm text-muted-foreground">{agent.description}</p>
              </div>
              <div className="flex flex-wrap justify-center gap-2">
                {(agent.trigger?.type === "event" ? [t("sampleEvent"), ...suggestions.slice(0, 2)] : suggestions).map((s) => (
                  <Button key={s} variant="outline" size="sm" onClick={() => void send(s)} disabled={suspended}>
                    {s}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {turns.map((turn) => {
            const live = streaming && turn.id === turns.at(-1)?.id;
            const isSelected = current?.id === turn.id;
            return (
              <div key={turn.id} className="space-y-3">
                <div className="flex justify-end gap-2">
                  <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
                    {turn.question}
                  </p>
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-muted" aria-hidden>
                    <User className="size-3.5" />
                  </span>
                </div>
                <div className="flex gap-2">
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary/15 text-primary" aria-hidden>
                    <Bot className="size-3.5" />
                  </span>
                  <div className="min-w-0 max-w-[85%] space-y-2">
                    <div
                      className={cn(
                        "whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-muted px-3.5 py-2 text-sm leading-relaxed",
                        turn.run.error && "bg-destructive/10 text-destructive",
                      )}
                    >
                      {turn.run.text ? (
                        <span className={cn(live && "ai-caret")}><RichText text={turn.run.text} /></span>
                      ) : turn.run.error ? null : (
                        <span className="inline-flex items-center gap-2 text-muted-foreground">
                          <Loader2 className="size-3.5 animate-spin" aria-hidden />
                          {turn.run.steps.length ? t("working") : t("thinking")}
                        </span>
                      )}
                      {turn.run.error && <span className="block">{turn.run.error}</span>}
                      {turn.run.stopReason === "max_steps" && (
                        <span className="mt-1 block text-xs text-amber-700 dark:text-amber-300">{t("maxSteps")}</span>
                      )}
                    </div>
                    {turn.run.artifacts.filter((a) => a.mime.startsWith("image/")).map((artifact) => (
                      <ArtifactImage key={artifact.url} artifact={artifact} />
                    ))}
                    <div className="flex flex-wrap items-center gap-2">
                      <Counter run={turn.run} live={live} />
                      {turn.run.steps.length > 0 && (
                        <button
                          type="button"
                          onClick={() => setSelected(turn.id)}
                          aria-pressed={isSelected}
                          className={cn(
                            "rounded-full border px-2 py-0.5 text-[0.7rem] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            isSelected ? "border-primary/50 bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted",
                          )}
                        >
                          {t("showSteps", { count: turn.run.steps.length })}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="border-t p-3"
        >
          <label htmlFor={inputId} className="sr-only">{t("inputLabel")}</label>
          <div className="flex items-end gap-2 rounded-xl border bg-background p-1.5 focus-within:ring-2 focus-within:ring-ring/50">
            <textarea
              id={inputId}
              ref={inputRef}
              rows={1}
              value={input}
              disabled={suspended}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder={suspended ? t("suspended") : t("placeholder")}
              className="max-h-40 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
            />
            {streaming ? (
              <Button type="button" size="icon" variant="secondary" onClick={() => abortRef.current?.abort()} aria-label={t("stop")} title={t("stop")}>
                <Square aria-hidden className="fill-current" />
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!input.trim() || suspended} aria-label={t("send")} title={t("send")}>
                <ArrowUp aria-hidden />
              </Button>
            )}
          </div>
        </form>
        <p role="status" className="sr-only">{announce}</p>
      </section>

      <section aria-labelledby="test-steps" className="flex max-h-[min(72vh,760px)] min-h-[320px] flex-col overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        <div className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
          <h2 id="test-steps" className="flex items-center gap-2 text-sm font-semibold">
            {t("stepsTitle")}
            {streaming && <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-emerald-500" />}
          </h2>
          {current?.run.traceUrl && (
            <a href={current.run.traceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
              {t("openTrace")}
              <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {!current || current.run.steps.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("noSteps")}</p>
          ) : (
            <ol aria-label={t("stepsOf", { question: current.question.slice(0, 60) })} className="relative before:absolute before:bottom-3 before:left-3.5 before:top-3 before:w-px before:bg-border">
              {current.run.steps.map((step) => <StepCard key={step.id} step={step} />)}
            </ol>
          )}
        </div>
        {current && (
          <div className="flex items-center justify-between gap-2 border-t bg-muted/30 px-4 py-2">
            <span className="text-xs text-muted-foreground">{t("thisMessage")}</span>
            <Counter run={current.run} live={streaming && current.id === turns.at(-1)?.id} />
          </div>
        )}
      </section>
    </div>
  );
}
