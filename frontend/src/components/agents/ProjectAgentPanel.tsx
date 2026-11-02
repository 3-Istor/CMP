"use client";

import { Clock, Loader2, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
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

const ACTION_KEYS: Record<string, string> = {
  read: "read",
  "project.create": "projectCreate",
  "app.create": "appCreate",
  "code.push": "codePush",
  "code.push_sensitive": "codePushSensitive",
  "release.cut": "releaseCut",
  "release.promote": "releasePromote",
  "release.retry": "releaseRetry",
  "release.skip": "releaseSkip",
  "release.abort": "releaseAbort",
  "release.settings": "releaseSettings",
  "app.delete": "appDelete",
};

const OUTCOME_CLASS: Record<string, { key: string; className: string }> = {
  allow: { key: "allow", className: "text-emerald-600 dark:text-emerald-400" },
  ask: { key: "ask", className: "text-sky-600 dark:text-sky-400" },
  pr: { key: "pr", className: "text-violet-600 dark:text-violet-400" },
  confirm: { key: "confirm", className: "text-amber-600 dark:text-amber-400" },
  deny: { key: "deny", className: "text-muted-foreground" },
};

export function ProjectAgentPanel({
  project,
  canConfigure,
}: {
  project: string;
  canConfigure: boolean;
}) {
  const t = useTranslations("agents.projectPanel");
  const locale = useLocale();
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
      toast.success(t("saved"));
    } catch {
      toast.error(t("adminsOnly"));
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
            {t("pendingTitle")}
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
        <h3 className="font-semibold">{t("stakeTitle")}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("stakeText")}{" "}
          <span className="font-medium text-foreground">
            {t(`levels.${settings.suggested_level}.label`)}
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
              <p className="font-medium">{t(`levels.${level}.label`)}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t(`levels.${level}.text`)}
              </p>
            </button>
          ))}
        </div>
      </section>

      {settings.level === "standard" && (
        <section className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-4">
          <Clock className="h-4 w-4 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{t("windowTitle")}</p>
            <p className="text-xs text-muted-foreground">
              {autonomous
                ? t("windowActive", {
                    time: new Date(settings.autonomous_until!).toLocaleTimeString(
                      locale,
                    ),
                  })
                : t("windowIdle")}
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
                {t("closeNow")}
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
                  {t("hours", { hours })}
                </Button>
              ))
            ))}
        </section>
      )}

      <section>
        <h3 className="font-semibold">
          {t("rulesTitle", { level: t(`levels.${settings.effective_level}.label`) })}
        </h3>
        <dl className="mt-3 divide-y divide-border rounded-xl border border-border bg-card">
          {Object.entries(settings.rules).map(([action, outcome]) => (
            <div
              key={action}
              className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm"
            >
              <dt>
                {ACTION_KEYS[action]
                  ? t(`actions.${ACTION_KEYS[action]}`)
                  : action}
              </dt>
              <dd className={cn("text-right font-medium", OUTCOME_CLASS[outcome]?.className)}>
                {OUTCOME_CLASS[outcome]
                  ? t(`outcomes.${OUTCOME_CLASS[outcome].key}`)
                  : outcome}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">{t("rulesNote")}</p>
      </section>
    </div>
  );
}
