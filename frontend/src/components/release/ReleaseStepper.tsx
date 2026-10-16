"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { ReleaseMetric, ReleaseStep, ReleaseView } from "@/types";
import { Check, Loader2, Pause, SkipForward, X } from "lucide-react";
import { useId, useState } from "react";
import {
  formatDuration,
  formatMetricValue,
  isVisibleStep,
  metricTone,
  STEP_KIND,
  TEXT_BAD,
  TEXT_OK,
} from "./format";

function Metrics({ metrics }: { metrics: ReleaseMetric[] }) {
  if (metrics.length === 0) return null;
  return (
    <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 gap-y-1 rounded-lg bg-muted/50 px-3 py-2 text-xs">
      {metrics.map((m) => {
        const tone = metricTone(m);
        return (
          <div key={m.name} className="contents">
            <dt className="truncate text-muted-foreground">{m.name}</dt>
            <dd
              className={cn(
                "text-right font-mono font-medium tabular-nums",
                tone === "ok" && TEXT_OK,
                tone === "bad" && TEXT_BAD,
              )}
            >
              {formatMetricValue(m.value, m.unit)}
              <span className="sr-only">
                {tone === "ok" ? " (conforme)" : tone === "bad" ? " (hors seuil)" : ""}
              </span>
            </dd>
            <dd className="text-right font-mono tabular-nums text-muted-foreground">
              {m.threshold != null
                ? `seuil ${formatMetricValue(m.threshold, m.unit)}`
                : ""}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function StepDot({ status }: { status: ReleaseStep["status"] }) {
  const base =
    "relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full border-2 bg-card";
  switch (status) {
    case "done":
      return (
        <span className={cn(base, "border-green-600 bg-green-600 text-white")}>
          <Check className="size-3.5" aria-hidden />
        </span>
      );
    case "failed":
      return (
        <span className={cn(base, "border-red-600 bg-red-600 text-white")}>
          <X className="size-3.5" aria-hidden />
        </span>
      );
    case "active":
      return (
        <span className={cn(base, "border-amber-500 text-amber-600 dark:text-amber-300")}>
          <Pause className="size-3" aria-hidden />
        </span>
      );
    case "running":
      return (
        <span className={cn(base, "border-primary text-primary")}>
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        </span>
      );
    case "skipped":
      return <span className={cn(base, "border-dashed border-muted-foreground/50")} />;
    default:
      return <span className={cn(base, "border-border")} />;
  }
}

const STATUS_SR: Record<ReleaseStep["status"], string> = {
  done: "terminée",
  active: "en pause, action requise",
  running: "en cours",
  pending: "à venir",
  failed: "en échec",
  skipped: "ignorée",
};

/** Argo Rollouts' messages say what happened, not what to do: say both. */
function stepMessage(message: string): string {
  if (/Inconclusive/i.test(message)) {
    return "Pas assez de trafic sur la nouvelle version pour conclure : le nombre de requêtes canary est resté sous le minimum. La release attend votre décision : passer l'étape, tout promouvoir ou abort.";
  }
  return message;
}

export function ReleaseStepper({
  view,
  canOperate,
  busy,
  onContinue,
  onAbort,
  onSkip,
}: {
  view: ReleaseView;
  canOperate: boolean;
  busy: boolean;
  onContinue: () => void;
  onAbort: () => void;
  onSkip: (reason: string) => Promise<boolean>;
}) {
  const [skipping, setSkipping] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const reasonId = useId();

  const submitSkip = async () => {
    if (await onSkip(reason.trim())) {
      setSkipping(null);
      setReason("");
    }
  };

  const visibleSteps = view.steps.filter(isVisibleStep);
  // A scale step in progress shows as a sub-line on the step it prepares.
  const preparing = new Map<number, ReleaseStep>();
  let pendingScale: ReleaseStep | null = null;
  for (const step of view.steps) {
    if (!isVisibleStep(step)) {
      if (step.status === "running" || step.status === "active" || step.status === "failed") {
        pendingScale = step;
      }
      continue;
    }
    if (pendingScale) {
      preparing.set(step.index, pendingScale);
      pendingScale = null;
    }
  }

  return (
    <ol className="relative space-y-1">
      {visibleSteps.map((step, i) => {
        const isLast = i === visibleSteps.length - 1;
        const scale = preparing.get(step.index);
        const isFinished = step.status === "done" || step.status === "failed";
        const time = isFinished
          ? formatDuration(step.durationSeconds)
          : step.status === "pending" && step.durationSeconds
            ? `≈ ${formatDuration(step.durationSeconds)}`
            : "";
        // "active" is an analysis that ended inconclusive and paused the
        // release: it waits for someone to decide, like a running one.
        const canSkip =
          canOperate &&
          view.actions.skip &&
          (step.status === "running" || step.status === "active") &&
          step.kind === "analysis";
        const showMetrics =
          step.metrics.length > 0 && (isFinished || step.status === "running");

        return (
          <li
            key={step.index}
            className="relative flex gap-3 pb-3"
            aria-current={step.status === "active" || step.status === "running" ? "step" : undefined}
          >
            {!isLast && (
              <span
                aria-hidden
                className={cn(
                  "absolute top-6 bottom-0 left-3 w-px -translate-x-1/2",
                  step.status === "done" ? "bg-green-600/60" : "bg-border",
                )}
              />
            )}
            <StepDot status={step.status} />
            <div
              className={cn(
                "min-w-0 flex-1 rounded-lg px-3 py-1.5",
                step.status === "active" && "border border-amber-500/40 bg-amber-500/5",
                step.status === "running" && "border border-primary/30 bg-primary/5",
                step.status === "failed" && "border border-red-600/40 bg-red-600/5",
                step.status === "skipped" && "opacity-60",
              )}
            >
              <div className="flex items-baseline gap-2 text-sm">
                <span className="w-14 shrink-0 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                  {STEP_KIND[step.kind] ?? step.kind}
                </span>
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate font-medium",
                    step.status === "skipped" && "line-through",
                  )}
                >
                  {step.label}
                </span>
                <span className="sr-only">, {STATUS_SR[step.status]}</span>
                {time && (
                  <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                    {time}
                  </span>
                )}
              </div>

              {scale && (
                <p
                  className={cn(
                    "mt-1 flex items-center gap-1.5 text-xs",
                    scale.status === "failed" ? TEXT_BAD : "text-muted-foreground",
                  )}
                >
                  {scale.status !== "failed" && (
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                  )}
                  Préparation des pods canary
                  {scale.label ? ` · ${scale.label}` : ""}
                  {scale.message ? ` · ${scale.message}` : ""}
                </p>
              )}

              {step.message && (
                <p className="mt-1 text-xs text-muted-foreground">{stepMessage(step.message)}</p>
              )}
              {step.status === "running" && !step.message && (
                <p className="mt-1 text-xs text-muted-foreground">En cours…</p>
              )}

              {showMetrics && <Metrics metrics={step.metrics} />}

              {step.status === "active" && step.kind === "pause" && canOperate && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {view.actions.promote && (
                    <Button size="sm" onClick={onContinue} disabled={busy}>
                      Continuer
                    </Button>
                  )}
                  {view.actions.abort && (
                    <Button size="sm" variant="destructive" onClick={onAbort} disabled={busy}>
                      Abort
                    </Button>
                  )}
                </div>
              )}

              {canSkip && skipping !== step.index && (
                <div className="mt-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setSkipping(step.index)}
                    disabled={busy}
                  >
                    <SkipForward />
                    Passer l&apos;étape
                  </Button>
                </div>
              )}

              {canSkip && skipping === step.index && (
                <form
                  className="mt-2 space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    submitSkip();
                  }}
                >
                  <p className="text-xs text-muted-foreground">
                    L&apos;analyse sera arrêtée et la release marquée « forcée ».
                  </p>
                  <Label htmlFor={reasonId} className="text-xs">
                    Motif
                  </Label>
                  <Input
                    id={reasonId}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    required
                    autoFocus
                    className="h-8"
                  />
                  <div className="flex gap-2">
                    <Button size="sm" type="submit" disabled={busy || !reason.trim()}>
                      Passer l&apos;étape
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      type="button"
                      onClick={() => {
                        setSkipping(null);
                        setReason("");
                      }}
                    >
                      Annuler
                    </Button>
                  </div>
                </form>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
