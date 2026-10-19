"use client";

import { Check, Globe, Lock, RotateCcw } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { cn } from "@/lib/utils";

import { Mascot, type MascotMood } from "./Mascot";

const CLIENTS = ["Claude", "ChatGPT", "Codex", "OpenCode", "Cursor"] as const;

type Line =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; detail: string }
  | { kind: "result" };

// A non-technical employee on their laptop. The tool names are the real
// ones of the CNP MCP server; the app and its URL are an example.
const SCRIPT: { line: Line; wait: number }[] = [
  {
    line: {
      kind: "user",
      text: "I need a page with the October seminar agenda and the speakers. Can you put it online for the team?",
    },
    wait: 900,
  },
  { line: { kind: "tool", name: "whoami", detail: "your CNP space" }, wait: 1100 },
  {
    line: {
      kind: "assistant",
      text: "You don't have a sandbox yet. Can I create “alice-sandbox” for your experiments?",
    },
    wait: 1500,
  },
  { line: { kind: "user", text: "Yes, go ahead." }, wait: 1200 },
  { line: { kind: "tool", name: "create_project", detail: "sandbox" }, wait: 1000 },
  { line: { kind: "tool", name: "create_app", detail: "static site" }, wait: 1000 },
  { line: { kind: "tool", name: "push_files", detail: "4 files" }, wait: 900 },
  { line: { kind: "tool", name: "cut_release", detail: "v0.1.0" }, wait: 900 },
  { line: { kind: "tool", name: "wait_for_app", detail: "running" }, wait: 1300 },
  {
    line: {
      kind: "assistant",
      text: "It's online, behind the company login. Want me to add the venue map?",
    },
    wait: 600,
  },
  { line: { kind: "result" }, wait: 4500 },
];

function subscribeReducedMotion(onChange: () => void) {
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function usePrefersReducedMotion() {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    () => true,
  );
}

export function AgentChatDemo() {
  const reduced = usePrefersReducedMotion();
  const [client, setClient] = useState<(typeof CLIENTS)[number]>("Claude");
  const [shown, setShown] = useState(SCRIPT.length);
  const [run, setRun] = useState(0);

  // The full conversation is on screen at rest (first paint, reduced
  // motion); the animation replays it line by line.
  useEffect(() => {
    if (reduced) return;
    let step = 0;
    let timer: ReturnType<typeof setTimeout>;
    const next = () => {
      setShown(step);
      if (step === SCRIPT.length) {
        timer = setTimeout(() => setRun((r) => r + 1), SCRIPT[step - 1].wait);
        return;
      }
      step += 1;
      timer = setTimeout(next, step === 1 ? 600 : SCRIPT[step - 2].wait);
    };
    timer = setTimeout(next, run === 0 ? 1500 : 0);
    return () => clearTimeout(timer);
  }, [reduced, run]);

  const visible = reduced ? SCRIPT.length : shown;

  const lines = SCRIPT.slice(0, visible).map((s) => s.line);
  const last = lines.at(-1);
  const mood: MascotMood =
    last?.kind === "result"
      ? "happy"
      : last?.kind === "tool" || last?.kind === "user"
        ? "working"
        : "idle";

  return (
    <div className="mx-auto w-full max-w-2xl text-left">
      <div className="mb-3 flex flex-wrap items-center justify-center gap-1.5">
        <span className="mr-1 text-xs text-muted-foreground">Works with</span>
        {CLIENTS.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setClient(name)}
            aria-pressed={client === name}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-primary",
              client === name
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-card text-muted-foreground hover:text-foreground",
            )}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-xl shadow-primary/5">
        <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-4 py-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/30" />
          <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/30" />
          <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/30" />
          <span className="ml-3 text-xs text-muted-foreground">
            {client} · CNP connected
          </span>
          <button
            type="button"
            onClick={() => setRun((r) => r + 1)}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Replay the demo"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Replay
          </button>
        </div>

        <div className="flex gap-4 p-4 sm:p-5">
          <Mascot mood={mood} className="hidden h-16 w-20 shrink-0 sm:block" />
          <ol
            className="flex min-h-[22rem] min-w-0 flex-1 flex-col gap-2.5"
            aria-live="polite"
          >
            {lines.map((line, i) => (
              <li
                key={`${run}-${i}`}
                className="motion-safe:animate-[chat-in_0.35s_ease-out]"
              >
                <ChatLine line={line} />
              </li>
            ))}
          </ol>
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-muted-foreground/70">
        Example conversation. The tools are the real CNP ones.
      </p>
    </div>
  );
}

function ChatLine({ line }: { line: Line }) {
  if (line.kind === "user") {
    return (
      <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
        {line.text}
      </p>
    );
  }
  if (line.kind === "assistant") {
    return (
      <p className="w-fit max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-3.5 py-2 text-sm">
        {line.text}
      </p>
    );
  }
  if (line.kind === "tool") {
    return (
      <p className="inline-flex items-center gap-2 rounded-md border border-border px-2.5 py-1 font-mono text-xs text-muted-foreground">
        <Check className="h-3.5 w-3.5 text-primary" />
        {line.name}
        <span className="text-muted-foreground/70">· {line.detail}</span>
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-primary/30 bg-primary/10 px-3.5 py-2.5 text-sm">
      <Globe className="h-4 w-4 text-primary" />
      <span className="font-mono text-xs">seminar-october.3istor.com</span>
      <span className="inline-flex items-center gap-1 rounded-full bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
        <Lock className="h-3 w-3" /> company login
      </span>
      <span className="ml-auto text-xs text-muted-foreground">4 min</span>
    </div>
  );
}
