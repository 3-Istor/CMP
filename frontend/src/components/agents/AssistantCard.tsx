"use client";

import { ArrowRight, Check, Copy, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getAgentSummary } from "@/lib/api";
import type { AgentSummary } from "@/types";

import { InstallButtons } from "./InstallButtons";
import { Mascot } from "./Mascot";

const ASKS = [
  "Put a page online with the agenda of our next team event.",
  "Give me a one-screen status of my projects this week.",
  "Why is my app slow? Read its logs and tell me before changing anything.",
];

function since(iso: string | null): string {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 60) return `${Math.max(minutes, 1)} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function AskChip({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard refused: nothing else to do, the text is on screen.
        }
      }}
      className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-left text-xs text-foreground transition-colors hover:bg-primary/10"
      title="Copy, then paste it in your assistant"
    >
      {copied ? (
        <Check className="h-3 w-3 shrink-0 text-primary" />
      ) : (
        <Copy className="h-3 w-3 shrink-0 text-muted-foreground" />
      )}
      <span className="truncate">{text}</span>
    </button>
  );
}

export function AssistantCard() {
  const [summary, setSummary] = useState<AgentSummary | null>(null);

  useEffect(() => {
    getAgentSummary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);

  if (!summary) return null;
  const connected = summary.assistants.length > 0;

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 sm:flex-row sm:items-start">
      <Mascot
        mood={connected ? "happy" : "idle"}
        className="h-14 w-16 shrink-0"
      />
      <div className="min-w-0 flex-1 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-semibold">Your AI assistant</h2>
            <p className="text-sm text-muted-foreground">
              {connected
                ? `${summary.apps_created} app${summary.apps_created === 1 ? "" : "s"} created through an assistant · last action ${since(summary.last_action_at)}`
                : "Ask Claude, ChatGPT or Codex to create and release apps for you, with your rights."}
            </p>
          </div>
          <Link
            href="/connect-ai"
            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            {connected ? "Connect another" : "Connect an assistant"}
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        {connected && (
          <div className="flex flex-wrap gap-1.5">
            {summary.assistants.map((a) => (
              <span
                key={a.id}
                className="inline-flex items-center gap-1.5 rounded-full border border-primary/40 px-2.5 py-0.5 text-xs text-primary"
              >
                {a.client_name}
                <span className="text-muted-foreground">
                  · {a.scopes.includes("cnp:write") ? "read & write" : "read only"}
                </span>
              </span>
            ))}
            {summary.pending_confirmations > 0 && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs text-amber-700 dark:text-amber-300">
                <ShieldAlert className="h-3 w-3" />
                {summary.pending_confirmations} waiting for your approval
              </span>
            )}
          </div>
        )}

        {!connected && <InstallButtons />}

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Try asking:</span>
          {ASKS.map((ask) => (
            <AskChip key={ask} text={ask} />
          ))}
        </div>
      </div>
    </section>
  );
}
