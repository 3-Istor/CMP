"use client";

import { Clock, Loader2, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  getAgentSettings,
  getProjectAgentConfirmations,
  updateAgentSettings,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AgentConfirmation, AgentSettings, StakeLevel } from "@/types";

const LEVELS: Record<StakeLevel, { label: string; text: string }> = {
  sandbox: {
    label: "Sandbox",
    text: "Personal experiments. The assistant just does it.",
  },
  demo: {
    label: "Demo",
    text: "No real users: breaking it for 10 minutes is fine. No confirmation.",
  },
  standard: {
    label: "Standard",
    text: "Real internal users. Releases and settings ask you first.",
  },
  critical: {
    label: "Critical",
    text: "Business data or external users. Code goes through a pull request, releases need an approval in the CMP.",
  },
};

const ACTIONS: Record<string, string> = {
  read: "Read status, logs, costs",
  "project.create": "Create a project",
  "app.create": "Create an app",
  "code.push": "Push application code",
  "code.push_sensitive": "Change CI, Dockerfile, deploy settings",
  "release.cut": "Release a version",
  "release.promote": "Promote a canary",
  "release.retry": "Retry a failed check",
  "release.skip": "Skip a check",
  "release.abort": "Abort a rollout",
  "release.settings": "Change release settings",
  "app.delete": "Delete an app",
};

const OUTCOME: Record<string, { label: string; className: string }> = {
  allow: { label: "On its own", className: "text-emerald-600 dark:text-emerald-400" },
  ask: { label: "Asks you in the chat", className: "text-sky-600 dark:text-sky-400" },
  pr: { label: "Pull request", className: "text-violet-600 dark:text-violet-400" },
  confirm: { label: "Approval in the CMP", className: "text-amber-600 dark:text-amber-400" },
  deny: { label: "Only in the CMP", className: "text-muted-foreground" },
};

export function ProjectAgentPanel({
  project,
  canConfigure,
}: {
  project: string;
  canConfigure: boolean;
}) {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [pending, setPending] = useState<AgentConfirmation[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getAgentSettings(project).then(setSettings).catch(() => setSettings(null));
    getProjectAgentConfirmations(project)
      .then((items) => setPending(items.filter((c) => c.status === "pending")))
      .catch(() => setPending([]));
  }, [project]);
  useEffect(load, [load]);

  const save = async (body: { level?: StakeLevel; autonomous_hours?: number }) => {
    setBusy(true);
    try {
      setSettings(await updateAgentSettings(project, body));
      toast.success("Saved");
    } catch {
      toast.error("Only project admins can change this.");
    } finally {
      setBusy(false);
    }
  };

  if (!settings) {
    return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  }

  const autonomous =
    settings.autonomous_until && new Date(settings.autonomous_until) > new Date();

  return (
    <div className="space-y-8">
      {pending.length > 0 && (
        <section className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            Waiting for an approval
          </h3>
          <ul className="mt-2 space-y-1.5 text-sm">
            {pending.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/connect/confirm/${c.id}`}
                  className="text-primary hover:underline"
                >
                  {c.summary}
                </Link>{" "}
                <span className="text-muted-foreground">({c.username})</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 className="font-semibold">What is at stake in this project</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Sets how freely AI assistants act here. Suggested:{" "}
          <span className="font-medium text-foreground">
            {LEVELS[settings.suggested_level].label}
          </span>{" "}
          ({settings.suggestion_reason})
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {settings.levels.map((level) => (
            <button
              key={level}
              type="button"
              disabled={!canConfigure || busy}
              onClick={() => save({ level })}
              aria-pressed={settings.level === level}
              className={cn(
                "rounded-xl border p-4 text-left transition-colors disabled:cursor-default",
                settings.level === level
                  ? "border-primary bg-primary/10"
                  : "border-border bg-card enabled:hover:border-primary/40",
              )}
            >
              <p className="font-medium">{LEVELS[level].label}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {LEVELS[level].text}
              </p>
            </button>
          ))}
        </div>
      </section>

      {settings.level === "standard" && (
        <section className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-4">
          <Clock className="h-4 w-4 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">Autonomous window</p>
            <p className="text-xs text-muted-foreground">
              {autonomous
                ? `Assistants act as on a demo project until ${new Date(settings.autonomous_until!).toLocaleTimeString()}.`
                : "Let assistants work without asking for a few hours, for a working session."}
            </p>
          </div>
          {canConfigure &&
            (autonomous ? (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => save({ autonomous_hours: 0 })}
              >
                Close now
              </Button>
            ) : (
              [1, 4, 8].map((hours) => (
                <Button
                  key={hours}
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => save({ autonomous_hours: hours })}
                >
                  {hours} h
                </Button>
              ))
            ))}
        </section>
      )}

      <section>
        <h3 className="font-semibold">
          What an assistant does here ({LEVELS[settings.effective_level].label})
        </h3>
        <dl className="mt-3 divide-y divide-border rounded-xl border border-border bg-card">
          {Object.entries(settings.rules).map(([action, outcome]) => (
            <div
              key={action}
              className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm"
            >
              <dt>{ACTIONS[action] ?? action}</dt>
              <dd className={cn("text-right font-medium", OUTCOME[outcome]?.className)}>
                {OUTCOME[outcome]?.label ?? outcome}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">
          While an alert fires on an app, aborting or retrying its rollout is
          always allowed. Every action is in the Activity tab, with the
          assistant that did it.
        </p>
      </section>
    </div>
  );
}
