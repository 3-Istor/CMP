"use client";

import { ArrowRight, Check, Copy, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { getAgentSummary } from "@/lib/api";
import type { AgentSummary } from "@/types";

import { InstallButtons } from "./InstallButtons";
import { Mascot } from "./Mascot";

function since(iso: string | null, t: (key: string, values?: Record<string, number>) => string): string {
  if (!iso) return t("never");
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 60) {
    return t("assistant.since.minutes", { count: Math.max(minutes, 1) });
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t("assistant.since.hours", { count: hours });
  return t("assistant.since.days", { count: Math.round(hours / 24) });
}

function AskChip({ text }: { text: string }) {
  const t = useTranslations("agents.assistant");
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
      title={t("copyHint")}
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
  const t = useTranslations("agents");
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
            <h2 className="font-semibold">{t("assistant.title")}</h2>
            <p className="text-sm text-muted-foreground">
              {connected
                ? t("assistant.connectedText", {
                    count: summary.apps_created,
                    since: since(summary.last_action_at, t),
                  })
                : t("assistant.emptyText")}
            </p>
          </div>
          <Link
            href="/connect-ai"
            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            {connected
              ? t("assistant.connectAnother")
              : t("assistant.connectOne")}
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
                  ·{" "}
                  {a.scopes.includes("cnp:write")
                    ? t("scopes.readWrite")
                    : t("scopes.readOnly")}
                </span>
              </span>
            ))}
            {summary.pending_confirmations > 0 && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs text-amber-700 dark:text-amber-300">
                <ShieldAlert className="h-3 w-3" />
                {t("assistant.waitingApproval", {
                  count: summary.pending_confirmations,
                })}
              </span>
            )}
          </div>
        )}

        {!connected && <InstallButtons />}

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {t("assistant.tryAsking")}
          </span>
          {(t.raw("assistant.asks") as string[]).map((ask) => (
            <AskChip key={ask} text={ask} />
          ))}
        </div>
      </div>
    </section>
  );
}
