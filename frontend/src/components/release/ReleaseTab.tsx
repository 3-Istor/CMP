"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { abortRelease, promoteRelease, skipReleaseStep } from "@/lib/api";
import { useCan, useRelease, useReleaseHistory } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { ReleaseEvent, ReleaseRevision, ReleaseView } from "@/types";
import { AlertTriangle, ArrowRight, CheckCircle2, Rocket, XCircle } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { ReleaseHistoryTable } from "./ReleaseHistoryTable";
import { ReleaseStepper } from "./ReleaseStepper";
import {
  actionLabel,
  errorMessage,
  formatDateTime,
  formatDuration,
  formatMetricValue,
  formatTime,
  imageTag,
  isVisibleStep,
  OUTCOME,
  PHASE,
  STRATEGY,
  TONE,
} from "./format";

type Confirming = null | "full" | "abort";

function DisabledCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Rocket className="h-4 w-4 text-muted-foreground" />
          Release non activée
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Cette application est mise à jour en rolling update : chaque nouvelle
          version remplace les pods directement. Pour la livrer progressivement,
          avec une part du trafic d&apos;abord, une analyse automatique des
          erreurs et une validation manuelle, ajoutez une section{" "}
          <code className="font-mono">release</code> dans{" "}
          <code className="font-mono">deploy/values.yaml</code> du dépôt de
          l&apos;application :
        </p>
        <pre className="overflow-x-auto rounded-lg bg-muted px-4 py-3 font-mono text-xs">
          {`release:
  strategy: canary   # rolling | canary | bluegreen
  preset: safe       # safe | fast | demo | manual`}
        </pre>
        <p className="text-muted-foreground">
          Une fois le changement mergé, Argo CD synchronise l&apos;application et
          la version suivante passe par les étapes du preset.
        </p>
      </CardContent>
    </Card>
  );
}

function Banner({ view }: { view: ReleaseView }) {
  const rev = view.revision != null ? ` #${view.revision}` : "";
  const stable = imageTag(view.stableImage);

  if (view.phase === "Aborted" || view.phase === "Degraded") {
    const failedStep = view.steps.find((s) => s.status === "failed");
    const failedMetrics =
      failedStep?.metrics.filter((m) => m.phase === "Failed" || m.phase === "Error") ?? [];
    const title = failedStep
      ? `Canary rejeté automatiquement · release${rev}`
      : view.phase === "Aborted"
        ? `Release${rev} abortée`
        : `Release${rev} dégradée`;
    return (
      <div role="alert" className={cn("flex gap-3 rounded-xl border p-4", TONE.bad)}>
        <XCircle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <div className="space-y-1 text-sm">
          <p className="font-semibold">{title}</p>
          {failedMetrics.map((m) => (
            <p key={m.name}>
              {m.name} a atteint {formatMetricValue(m.value, m.unit)}
              {m.threshold != null &&
                ` pour un seuil de ${formatMetricValue(m.threshold, m.unit)}`}
              .
            </p>
          ))}
          <p className="text-foreground/80">
            Le stable {stable} sert 100 % du trafic. Pour corriger, poussez un
            nouveau commit : la prochaine version repartira en canary.
          </p>
        </div>
      </div>
    );
  }

  if (view.phase === "Healthy" && view.forced) {
    return (
      <div role="status" className={cn("flex gap-3 rounded-xl border p-4", TONE.warn)}>
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <div className="space-y-1 text-sm">
          <p className="font-semibold">Release{rev} promue de force</p>
          <p className="text-foreground/80">
            {stable} sert 100 % du trafic. Des étapes ont été ignorées, le motif
            est enregistré dans l&apos;historique.
          </p>
        </div>
      </div>
    );
  }

  if (view.phase === "Healthy" && view.steps.some(isVisibleStep)) {
    return (
      <div role="status" className={cn("flex gap-3 rounded-xl border p-4", TONE.ok)}>
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <p className="text-sm">
          <span className="font-semibold">Release{rev} promue</span>
          <span className="text-foreground/80"> · {stable} est le stable.</span>
        </p>
      </div>
    );
  }

  return null;
}

function TrafficSplit({ view }: { view: ReleaseView }) {
  const canary = view.weights?.canary ?? 0;
  const stable = view.weights?.stable ?? 100 - canary;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Répartition du trafic</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          role="img"
          aria-label={`Stable ${stable} %, canary ${canary} %`}
          className="flex h-7 overflow-hidden rounded-md bg-muted font-mono text-xs font-medium"
        >
          {stable > 0 && (
            <div
              className="flex items-center justify-center bg-primary text-primary-foreground transition-[width] duration-500"
              style={{ width: `${stable}%` }}
            >
              {stable >= 10 && `${stable} %`}
            </div>
          )}
          {canary > 0 && (
            <div
              className="flex items-center justify-center bg-amber-500 text-amber-950 transition-[width] duration-500"
              style={{ width: `${canary}%` }}
            >
              {canary >= 10 && `${canary} %`}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <i className="size-2.5 rounded-sm bg-primary" aria-hidden />
            Stable {imageTag(view.stableImage)}
          </span>
          {view.canaryHash && view.canaryHash !== view.stableHash && (
            <span className="flex items-center gap-1.5">
              <i className="size-2.5 rounded-sm bg-amber-500" aria-hidden />
              Canary {imageTag(view.canaryImage)}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Pods({ ready, desired }: { ready: number; desired: number }) {
  const shown = Math.min(desired, 10);
  return (
    <span className="flex items-center gap-2">
      <span className="flex gap-0.5" aria-hidden>
        {Array.from({ length: shown }, (_, i) => (
          <i
            key={i}
            className={cn(
              "size-2 rounded-full",
              i < ready ? "bg-green-600" : "border border-muted-foreground/40",
            )}
          />
        ))}
      </span>
      <span className="font-mono text-xs tabular-nums">
        {ready}/{desired}
        <span className="sr-only"> pods prêts</span>
      </span>
    </span>
  );
}

function Revisions({ revisions }: { revisions: ReleaseRevision[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Révisions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {revisions.length === 0 && (
          <p className="text-sm text-muted-foreground">Aucun ReplicaSet actif.</p>
        )}
        {revisions.map((r) => (
          <div key={r.hash} className="flex items-center gap-2 text-sm">
            <Badge
              variant="outline"
              className={r.role === "canary" ? TONE.warn : TONE.info}
            >
              {r.role}
            </Badge>
            <span className="min-w-0 flex-1 truncate font-mono text-xs" title={r.image}>
              {imageTag(r.image)} · {r.hash}
            </span>
            <Pods ready={r.ready} desired={r.desired} />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function Activity({ event }: { event: ReleaseEvent | undefined }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Activité</CardTitle>
      </CardHeader>
      <CardContent>
        {!event ? (
          <p className="text-sm text-muted-foreground">
            Aucune activité enregistrée pour cette release.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            <li className="flex gap-3">
              <time className="w-11 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                {formatTime(event.started_at)}
              </time>
              <span>Release #{event.revision} démarrée</span>
            </li>
            {[...(event.actors ?? [])]
              .sort((a, b) => a.at.localeCompare(b.at))
              .map((a, i) => (
                <li key={`${a.at}-${i}`} className="flex gap-3">
                  <time className="w-11 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                    {formatTime(a.at)}
                  </time>
                  <span>
                    <span className="font-medium">{a.user}</span> {actionLabel(a.action)}
                    {a.reason && (
                      <span className="text-muted-foreground"> · motif : « {a.reason} »</span>
                    )}
                  </span>
                </li>
              ))}
            {event.finished_at && (
              <li className="flex gap-3">
                <time className="w-11 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                  {formatTime(event.finished_at)}
                </time>
                <span>{OUTCOME[event.outcome]?.label ?? event.outcome}</span>
              </li>
            )}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function ReleaseTab({
  deploymentId,
  projectName,
}: {
  deploymentId: number;
  projectName: string;
}) {
  const { view, error, live, refresh } = useRelease(deploymentId);
  const {
    events,
    loading: historyLoading,
    refresh: refreshHistory,
  } = useReleaseHistory(deploymentId);
  const { can } = useCan(projectName);
  const canOperate = can("release.operate");

  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [reason, setReason] = useState("");
  const reasonId = useId();

  const transitionKey = view ? `${view.revision}:${view.phase}:${view.forced}` : null;
  useEffect(() => {
    if (transitionKey) refreshHistory();
  }, [transitionKey, refreshHistory]);

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try {
      await action();
      toast.success(success);
      await Promise.all([refresh(), refreshHistory()]);
      return true;
    } catch (e) {
      toast.error(errorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const closeConfirm = () => {
    setConfirming(null);
    setReason("");
  };

  const submitConfirm = async () => {
    const trimmed = reason.trim();
    const ok =
      confirming === "full"
        ? await run(
            () => promoteRelease(deploymentId, "full", trimmed),
            "Release promue à 100 %",
          )
        : await run(
            () => abortRelease(deploymentId, trimmed || null),
            "Release abortée, le stable sert 100 % du trafic",
          );
    if (ok) closeConfirm();
  };

  if (!view) {
    if (error) {
      return (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            Impossible de lire l&apos;état de la release : {errorMessage(new Error(error))}
          </CardContent>
        </Card>
      );
    }
    return (
      <div className="space-y-4">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }

  if (!view.enabled) {
    return (
      <div className="space-y-6">
        <DisabledCard />
        {events.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Historique des releases</CardTitle>
            </CardHeader>
            <CardContent>
              <ReleaseHistoryTable events={events} />
            </CardContent>
          </Card>
        )}
      </div>
    );
  }

  const phase = view.phase ? PHASE[view.phase] : null;
  const hasCanary = Boolean(view.canaryHash && view.canaryHash !== view.stableHash);
  const visibleSteps = view.steps.filter(isVisibleStep);
  const stepTotal = visibleSteps.length;
  const currentIndex = view.currentStepIndex;
  const stepCurrent =
    currentIndex != null && stepTotal > 0
      ? Math.max(
          1,
          Math.min(
            visibleSteps.filter((s) => s.index <= currentIndex).length +
              (visibleSteps.some((s) => s.index === currentIndex) ? 0 : 1),
            stepTotal,
          ),
        )
      : null;
  const currentEvent =
    events.find((e) => e.revision === view.revision) ??
    events.find((e) => e.outcome === "running");
  const eta =
    view.etaSeconds != null
      ? `≈ ${formatDuration(view.etaSeconds)}`
      : view.phase === "Paused"
        ? "après validation"
        : "-";

  return (
    <div className="space-y-6">
      <Banner view={view} />

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="mr-1 text-lg font-semibold">
              Release{view.revision != null && ` #${view.revision}`}
            </h2>
            {phase && (
              <Badge variant="outline" className={TONE[phase.tone]}>
                {phase.label}
              </Badge>
            )}
            {view.forced && (
              <Badge variant="outline" className={TONE.warn}>
                Forcée
              </Badge>
            )}
            {view.strategy && (
              <Badge variant="secondary">
                {STRATEGY[view.strategy] ?? view.strategy}
                {view.preset && ` · ${view.preset}`}
              </Badge>
            )}
            <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
              <span
                aria-hidden
                className={cn(
                  "size-1.5 rounded-full",
                  live === "stream" ? "bg-green-600" : "bg-muted-foreground/50",
                )}
              />
              {live === "stream"
                ? "Temps réel"
                : live === "polling"
                  ? "Actualisé toutes les 3 s"
                  : "Connexion…"}
            </span>
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <div className="col-span-2 min-w-0">
              <dt className="text-xs text-muted-foreground">Version</dt>
              <dd className="flex items-center gap-1.5 truncate font-mono">
                {hasCanary ? (
                  <>
                    <span title={view.stableImage ?? undefined}>
                      {imageTag(view.stableImage)}
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-label="vers" />
                    <span title={view.canaryImage ?? undefined}>
                      {imageTag(view.canaryImage)}
                    </span>
                  </>
                ) : (
                  <span title={view.stableImage ?? undefined}>
                    {imageTag(view.stableImage)}
                  </span>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Étape</dt>
              <dd className="font-mono tabular-nums">
                {stepCurrent != null ? `${stepCurrent}/${stepTotal}` : "-"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Fin estimée</dt>
              <dd>{eta}</dd>
            </div>
            {view.startedAt && (
              <div className="col-span-2 sm:col-span-4">
                <dt className="sr-only">Démarrée</dt>
                <dd className="text-xs text-muted-foreground">
                  Démarrée le {formatDateTime(view.startedAt)}
                </dd>
              </div>
            )}
          </dl>

          {canOperate &&
            confirming === null &&
            (view.actions.promoteFull || view.actions.abort) && (
              <div className="flex flex-wrap gap-2">
                {view.actions.promoteFull && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setConfirming("full")}
                    disabled={busy}
                  >
                    Tout promouvoir
                  </Button>
                )}
                {view.actions.abort && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setConfirming("abort")}
                    disabled={busy}
                  >
                    Abort
                  </Button>
                )}
              </div>
            )}

          {canOperate && confirming !== null && (
            <form
              className={cn(
                "max-w-lg space-y-2 rounded-lg border p-3",
                confirming === "full" ? TONE.warn : TONE.bad,
              )}
              onSubmit={(e) => {
                e.preventDefault();
                submitConfirm();
              }}
            >
              <p className="text-sm font-semibold">
                {confirming === "full"
                  ? "Tout promouvoir maintenant ?"
                  : "Aborter la release ?"}
              </p>
              <p className="text-xs text-foreground/80">
                {confirming === "full"
                  ? "Les étapes restantes, analyses comprises, seront ignorées. La release sera marquée « forcée »."
                  : "Le trafic repasse à 100 % sur le stable. La version rejetée reste dans Git jusqu'au prochain commit."}
              </p>
              <Label htmlFor={reasonId} className="text-xs text-foreground">
                {confirming === "full" ? "Motif" : "Motif (facultatif)"}
              </Label>
              <Input
                id={reasonId}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required={confirming === "full"}
                autoFocus
                className="h-8 bg-background text-foreground"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  type="submit"
                  variant={confirming === "abort" ? "destructive" : "default"}
                  disabled={busy || (confirming === "full" && !reason.trim())}
                >
                  Confirmer
                </Button>
                <Button size="sm" variant="ghost" type="button" onClick={closeConfirm}>
                  Annuler
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-baseline gap-2 text-sm">
              Étapes
              {view.preset && (
                <span className="text-xs font-normal text-muted-foreground">
                  preset {view.preset}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {stepTotal === 0 ? (
              <p className="text-sm text-muted-foreground">
                Aucune étape progressive pour cette stratégie.
              </p>
            ) : (
              <ReleaseStepper
                view={view}
                canOperate={canOperate}
                busy={busy}
                onContinue={() =>
                  run(() => promoteRelease(deploymentId, "next", null), "Étape suivante lancée")
                }
                onAbort={() => setConfirming("abort")}
                onSkip={(skipReason) =>
                  run(
                    () => skipReleaseStep(deploymentId, skipReason),
                    "Étape passée, release marquée « forcée »",
                  )
                }
              />
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <TrafficSplit view={view} />
          <Revisions revisions={view.revisions} />
          <Activity event={currentEvent} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Historique des releases</CardTitle>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <ReleaseHistoryTable events={events} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
