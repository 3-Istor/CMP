"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { createSilence, expireSilence, getObsAlerts } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { ObsAlert } from "@/types/observability";
import { BellOff, BookOpen, ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { toast } from "sonner";
import { CardState, SELECT_CLASS, Section, useObsFormat } from "./shared";

const STATE_CLASS: Record<string, string> = {
  active: "border-red-600/40 bg-red-600/10 text-red-700 dark:text-red-300",
  suppressed: "text-muted-foreground",
  unprocessed: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200",
};
const SEVERITY_CLASS: Record<string, string> = {
  critical: "border-red-600/40 text-red-700 dark:text-red-300",
  warning: "border-amber-500/40 text-amber-800 dark:text-amber-200",
};

/** Durations offered, in hours, cut at the platform's longest silence. */
export function durationChoices(maxHours: number): number[] {
  return [1, 2, 4, 8, 24, 72, 168].filter((h) => h <= maxHours);
}

function messageOf(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  try {
    const detail = JSON.parse(text).detail;
    return typeof detail === "string" ? detail : text;
  } catch {
    return text;
  }
}

export function MuteDialog({
  open,
  onOpenChange,
  project,
  app,
  alertname,
  maxHours,
  onMuted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  app: string;
  alertname: string;
  maxHours: number;
  onMuted: () => void;
}) {
  const t = useTranslations("obs.mute");
  const ids = { duration: useId(), comment: useId(), error: useId() };
  const choices = durationChoices(maxHours);
  const [hours, setHours] = useState(choices.includes(4) ? 4 : (choices[0] ?? 1));
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      await createSilence({
        project,
        matchers: { alertname, cnp_app: app },
        hours,
        comment,
      });
      toast.success(t("done"));
      onMuted();
      onOpenChange(false);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setPending(false);
    }
  };

  const label = (h: number) =>
    h % 24 === 0 ? t("days", { count: h / 24 }) : t("hours", { count: h });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title", { name: alertname })}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <p className="text-sm text-muted-foreground">
            {t("scope", { alert: alertname, app })}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor={ids.duration}>{t("duration")}</Label>
            <select
              id={ids.duration}
              className={cn(SELECT_CLASS, "w-full")}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
            >
              {choices.map((h) => (
                <option key={h} value={h}>
                  {label(h)}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.comment}>{t("comment")}</Label>
            <Input
              id={ids.comment}
              value={comment}
              maxLength={500}
              placeholder={t("commentPlaceholder")}
              onChange={(e) => setComment(e.target.value)}
              aria-describedby={error ? ids.error : undefined}
            />
          </div>
          {error && (
            <p id={ids.error} role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={pending}>
              <BellOff aria-hidden />
              {t("confirm")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AlertItem({
  alert,
  onMute,
}: {
  alert: ObsAlert;
  onMute: (alertname: string) => void;
}) {
  const t = useTranslations("obs.alerts");
  const { time } = useObsFormat();
  const name = alert.labels.alertname ?? "";
  const severity = alert.labels.severity ?? "";
  return (
    <li
      className={cn(
        "flex flex-col gap-2 rounded-xl border p-3 sm:flex-row sm:items-start",
        alert.state === "active" && "border-red-600/40 bg-red-600/5",
      )}
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{name}</span>
          <Badge variant="outline" className={STATE_CLASS[alert.state]}>
            {t.has(`state.${alert.state}`) ? t(`state.${alert.state}`) : alert.state}
          </Badge>
          {severity && (
            <Badge variant="outline" className={SEVERITY_CLASS[severity]}>
              {severity}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">
            {t("since", { time: time(alert.starts_at) })}
          </span>
        </div>
        {alert.summary && <p className="text-sm">{alert.summary}</p>}
        {alert.description && (
          <p className="text-xs text-muted-foreground">{alert.description}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {alert.runbook_url && (
          <a
            href={alert.runbook_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            <BookOpen className="h-4 w-4" aria-hidden />
            {t("runbook")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        )}
        {alert.state !== "suppressed" && (
          <Button size="sm" variant="outline" onClick={() => onMute(name)}>
            <BellOff aria-hidden />
            {t("mute")}
          </Button>
        )}
      </div>
    </li>
  );
}

/**
 * The app's alerts in the alert chain with a mute button, and the silences
 * covering it. `initialSilence` opens the mute dialog on that alert: the
 * Discord message's link lands here with `?silence=<alertname>`.
 */
export function AlertsView({
  appId,
  project,
  app,
  maxHours,
  initialSilence,
  compact = false,
}: {
  appId: number;
  project: string;
  app: string;
  maxHours: number;
  initialSilence?: string | null;
  compact?: boolean;
}) {
  const t = useTranslations("obs.alerts");
  const { time } = useObsFormat();
  const card = useCardData(`obs-alerts-${appId}`, () => getObsAlerts(appId), {
    intervalMs: 30_000,
  });
  // Pages key this view on ?silence=, so a new link opens a new dialog.
  const [muting, setMuting] = useState<string | null>(initialSilence ?? null);

  const lift = async (id: string) => {
    try {
      await expireSilence(project, id);
      toast.success(t("expired"));
      card.retry();
    } catch (err) {
      toast.error(messageOf(err));
    }
  };

  return (
    <Section title={t("title")} description={compact ? undefined : t("description")}>
      <CardState card={card} signal="alerts" height="h-24">
        {(data) => (
          <div className="space-y-4">
            {data.alerts.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("none")}</p>
            ) : (
              <ul className="space-y-2">
                {data.alerts.map((a) => (
                  <AlertItem key={a.fingerprint} alert={a} onMute={setMuting} />
                ))}
              </ul>
            )}
            {!compact && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">{t("silences")}</h3>
                {data.silences.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("noSilences")}</p>
                ) : (
                  <ul className="divide-y rounded-xl border">
                    {data.silences.map((s) => {
                      const shown = s.matchers.filter((m) => m.name !== "cnp_project");
                      return (
                        <li
                          key={s.id}
                          className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"
                        >
                          <div className="min-w-0 space-y-0.5">
                            <p className="font-mono text-xs">
                              {shown.length
                                ? shown.map((m) => `${m.name}=${m.value}`).join(", ")
                                : t("projectWide")}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {t("until", { time: time(s.ends_at) })}{" "}
                              {s.created_by && t("by", { who: s.created_by })}
                              {s.comment && `. ${s.comment}`}
                            </p>
                          </div>
                          <Button size="sm" variant="ghost" onClick={() => void lift(s.id)}>
                            {t("expire")}
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
      </CardState>
      {muting && (
        <MuteDialog
          open
          onOpenChange={(open) => !open && setMuting(null)}
          project={project}
          app={app}
          alertname={muting}
          maxHours={maxHours}
          onMuted={card.retry}
        />
      )}
    </Section>
  );
}
