"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  acceptPromotionStep,
  approvePromotion,
  cancelPromotion,
  cancelRun,
  getEnvironments,
  isNotConnected,
  listPromotions,
  listRuns,
  startPromotion,
  startRun,
  wakeStaging,
} from "@/lib/api";
import { NotConnected } from "@/components/clusters/NotConnected";
import { cn } from "@/lib/utils";
import type {
  EnvironmentState,
  EnvironmentsView,
  Promotion,
  PromotionStep,
  RunStatus,
  VerificationRun,
} from "@/types";
import { AlertTriangle, ArrowRight, CheckCircle2, Circle, Loader2, Play, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage, formatDateTime, TONE } from "../release/format";
import { PromotionPolicyCard } from "./PromotionPolicyCard";
import { StagingSettingsCard } from "./StagingSettingsCard";

const POLL_MS = 5000;

const RUN_STATUS: Record<RunStatus, { label: string; tone: keyof typeof TONE }> = {
  running: { label: "En cours", tone: "info" },
  passed: { label: "OK", tone: "ok" },
  failed: { label: "Échec", tone: "bad" },
  inconclusive: { label: "Inconclusif", tone: "warn" },
  cancelled: { label: "Annulé", tone: "muted" },
  error: { label: "Erreur", tone: "bad" },
};

const PROMOTION_STATUS: Record<Promotion["status"], { label: string; tone: keyof typeof TONE }> = {
  running: { label: "En cours", tone: "info" },
  waiting_approval: { label: "En attente", tone: "warn" },
  done: { label: "En prod", tone: "ok" },
  failed: { label: "Échouée", tone: "bad" },
  cancelled: { label: "Annulée", tone: "muted" },
};

function shortDigest(digest: string): string {
  const at = digest.indexOf("@sha256:");
  return at === -1 ? digest : `sha256:${digest.slice(at + 8, at + 20)}`;
}

function Lane({ env, onWake }: { env: EnvironmentState; onWake?: () => void }) {
  const staging = env.name === "staging";
  return (
    <div className="min-w-0 flex-1 space-y-2 rounded-lg border bg-muted/30 p-4">
      <div className="flex items-center justify-between gap-2">
        <Badge
          variant="outline"
          className={staging ? TONE.warn : TONE.ok}
        >
          {staging ? "STAGING" : "PROD"}
        </Badge>
        <span className="truncate text-xs text-muted-foreground">{env.hostname ?? env.namespace}</span>
      </div>
      <p className="font-mono text-xl">{env.version ?? "-"}</p>
      {Object.entries(env.digests).map(([component, digest]) => (
        <p key={component} className="truncate font-mono text-xs text-muted-foreground">
          {component} · {shortDigest(digest)}
        </p>
      ))}
      <p className="text-xs">
        {!env.reachable
          ? "Cluster injoignable"
          : env.asleep
            ? "En veille (offhours)"
            : env.converged
              ? "Une seule version, pods prêts"
              : "Déploiement en cours ou pods pas prêts"}
      </p>
      {staging && env.asleep && onWake && (
        <Button variant="outline" size="sm" onClick={onWake}>
          Réveiller
        </Button>
      )}
    </div>
  );
}

function StepIcon({ status }: { status: PromotionStep["status"] }) {
  if (status === "passed" || status === "accepted")
    return <CheckCircle2 className="h-4 w-4 text-green-600" />;
  if (status === "failed") return <XCircle className="h-4 w-4 text-red-600" />;
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-sky-600" />;
  if (status === "waiting") return <AlertTriangle className="h-4 w-4 text-amber-500" />;
  return <Circle className="h-4 w-4 text-muted-foreground" />;
}

const STEP_LABEL: Record<PromotionStep["kind"], string> = {
  run: "Vérification",
  soak: "Temps en staging",
  approve: "Approbation",
  promote: "Mise en prod",
};

function PromotionCard({
  deploymentId,
  promotion,
  view,
  onChanged,
}: {
  deploymentId: number;
  promotion: Promotion;
  view: EnvironmentsView;
  onChanged: () => void;
}) {
  const act = async (action: () => Promise<unknown>) => {
    try {
      await action();
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  const status = PROMOTION_STATUS[promotion.status];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          Promotion de {promotion.version ?? "la release"} vers la prod
          <Badge variant="outline" className={TONE[status.tone]}>
            {status.label}
          </Badge>
          {promotion.emergency && (
            <Badge variant="outline" className={TONE.bad}>
              Urgence
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ol className="space-y-2">
          {promotion.steps.map((step, index) => (
            <li key={index} className="flex items-start gap-3">
              <StepIcon status={step.status} />
              <div className="min-w-0 flex-1">
                <p>
                  <span className="font-medium">{STEP_LABEL[step.kind]}</span>
                  {step.kind === "run" && <span className="font-mono"> · {step.name}</span>}
                </p>
                {step.detail && <p className="break-words text-xs text-muted-foreground">{step.detail}</p>}
              </div>
              {step.kind === "run" && step.status === "waiting" && view.canAdminister && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => act(() => acceptPromotionStep(deploymentId, promotion.id, index))}
                >
                  Accepter
                </Button>
              )}
              {step.kind === "approve" && step.status === "waiting" && view.canAdminister && (
                <Button size="sm" onClick={() => act(() => approvePromotion(deploymentId, promotion.id))}>
                  Approuver
                </Button>
              )}
            </li>
          ))}
        </ol>
        {promotion.detail && <p className="text-xs text-muted-foreground">{promotion.detail}</p>}
        <p className="text-xs text-muted-foreground">
          Demandée par {promotion.actor} le {formatDateTime(promotion.createdAt)}
          {promotion.reason ? ` · ${promotion.reason}` : ""}
        </p>
        {(promotion.status === "running" || promotion.status === "waiting_approval") && view.canOperate && (
          <Button variant="ghost" size="sm" onClick={() => act(() => cancelPromotion(deploymentId, promotion.id))}>
            Annuler la promotion
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function RunsCard({
  deploymentId,
  view,
  runs,
  onChanged,
}: {
  deploymentId: number;
  view: EnvironmentsView;
  runs: VerificationRun[];
  onChanged: () => void;
}) {
  const suites = ["smoke", ...view.policy.suites.map((s) => s.name)];
  const launch = async (suite: string) => {
    try {
      await startRun(deploymentId, "staging", suite);
      toast.success(`Suite ${suite} lancée sur staging`);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Suites de vérification sur staging</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {view.canOperate && (
          <div className="flex flex-wrap gap-2">
            {suites.map((suite) => (
              <Button key={suite} variant="outline" size="sm" onClick={() => launch(suite)}>
                <Play className="mr-1 h-3 w-3" />
                {suite}
              </Button>
            ))}
          </div>
        )}
        {runs.length === 0 ? (
          <p className="text-muted-foreground">Aucune suite lancée sur staging pour l&apos;instant.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 font-medium">Suite</th>
                  <th className="py-1 pr-3 font-medium">Version</th>
                  <th className="py-1 pr-3 font-medium">Résultat</th>
                  <th className="py-1 pr-3 font-medium">Détail</th>
                  <th className="py-1 pr-3 font-medium">Lancée</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 15).map((run) => (
                  <tr key={run.id} className="border-t align-top">
                    <td className="py-1.5 pr-3 font-mono">{run.suite}</td>
                    <td className="py-1.5 pr-3 font-mono">{run.version ?? "-"}</td>
                    <td className="py-1.5 pr-3">
                      <Badge variant="outline" className={TONE[RUN_STATUS[run.status].tone]}>
                        {RUN_STATUS[run.status].label}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-3">
                      {run.summary}
                      {run.detailsUrl && (
                        <a className="ml-1 underline" href={run.detailsUrl} target="_blank" rel="noreferrer">
                          logs
                        </a>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 whitespace-nowrap">
                      {formatDateTime(run.startedAt)} · {run.actor}
                    </td>
                    <td className="py-1.5">
                      {run.status === "running" && view.canOperate && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => cancelRun(deploymentId, run.id).then(onChanged, (e) => toast.error(errorMessage(e)))}
                        >
                          Annuler
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function HistoryCard({
  deploymentId,
  view,
  promotions,
  onChanged,
}: {
  deploymentId: number;
  view: EnvironmentsView;
  promotions: Promotion[];
  onChanged: () => void;
}) {
  const prod = view.environments.find((e) => e.name === "prod");
  const goBack = async (promotion: Promotion) => {
    try {
      await startPromotion(deploymentId, { rollbackTo: promotion.id });
      toast.success(`Retour à ${promotion.version} lancé`);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  if (promotions.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Historique des promotions</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-medium">Version</th>
              <th className="py-1 pr-3 font-medium">Statut</th>
              <th className="py-1 pr-3 font-medium">Par</th>
              <th className="py-1 pr-3 font-medium">Quand</th>
              <th className="py-1 pr-3 font-medium">Commit</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {promotions.map((promotion) => {
              const status = PROMOTION_STATUS[promotion.status];
              const current =
                prod && JSON.stringify(prod.digests) === JSON.stringify(promotion.digests);
              return (
                <tr key={promotion.id} className="border-t">
                  <td className="py-1.5 pr-3 font-mono">
                    {promotion.version ?? "-"}
                    {promotion.rollbackOf ? " (retour)" : ""}
                  </td>
                  <td className="py-1.5 pr-3">
                    <Badge variant="outline" className={TONE[status.tone]}>
                      {status.label}
                    </Badge>
                  </td>
                  <td className="py-1.5 pr-3">{promotion.actor}</td>
                  <td className="py-1.5 pr-3 whitespace-nowrap">{formatDateTime(promotion.createdAt)}</td>
                  <td className="py-1.5 pr-3 font-mono">{promotion.commitSha?.slice(0, 8) ?? "-"}</td>
                  <td className="py-1.5">
                    {promotion.status === "done" && !current && view.canOperate && !view.activePromotion && (
                      <Button variant="ghost" size="sm" onClick={() => goBack(promotion)}>
                        Revenir à cette version
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

export function EnvironmentsTab({ deploymentId }: { deploymentId: number }) {
  const [view, setView] = useState<EnvironmentsView | null>(null);
  const [runs, setRuns] = useState<VerificationRun[]>([]);
  const [promotions, setPromotions] = useState<Promotion[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [emergency, setEmergency] = useState(false);
  const [reason, setReason] = useState("");
  const [starting, setStarting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await getEnvironments(deploymentId);
      setView(next);
      setError(null);
      if (next.staging?.state === "ready") {
        const [nextRuns, nextPromotions] = await Promise.all([
          listRuns(deploymentId, "staging"),
          listPromotions(deploymentId),
        ]);
        setRuns(nextRuns);
        setPromotions(nextPromotions);
      }
    } catch (e) {
      setError(e);
    }
  }, [deploymentId]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  if (error != null && !view) {
    const nc = isNotConnected(error);
    if (nc) return <NotConnected cloud={nc.cloud} />;
    return (
      <p className={cn("rounded-md border px-3 py-2 text-sm", TONE.bad)}>{errorMessage(error)}</p>
    );
  }
  if (!view) return <Skeleton className="h-48 w-full" />;

  const staging = view.environments.find((e) => e.name === "staging");
  const prod = view.environments.find((e) => e.name === "prod");
  const promotable =
    staging?.converged && prod && JSON.stringify(staging.digests) !== JSON.stringify(prod.digests);

  const promote = async () => {
    setStarting(true);
    try {
      await startPromotion(deploymentId, emergency ? { emergency: true, reason } : {});
      setEmergency(false);
      setReason("");
      refresh();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="space-y-6">
      {view.staging?.state === "ready" && staging && prod && (
        <Card>
          <CardContent className="space-y-4 pt-6">
            <div className="flex flex-col gap-3 md:flex-row md:items-stretch">
              <Lane
                env={staging}
                onWake={() =>
                  wakeStaging(deploymentId).then(
                    () => toast.success("Staging se réveille"),
                    (e) => toast.error(errorMessage(e)),
                  )
                }
              />
              <div className="flex items-center justify-center text-muted-foreground">
                <ArrowRight className="h-6 w-6 rotate-90 md:rotate-0" />
              </div>
              <Lane env={prod} />
            </div>
            {view.canOperate && !view.activePromotion && (
              <div className="space-y-3">
                {emergency && (
                  <div className="space-y-1">
                    <Label htmlFor="emergency-reason">Raison de l&apos;urgence (tracée et notifiée)</Label>
                    <Input
                      id="emergency-reason"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="Correctif du paiement en échec en prod"
                    />
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={promote} disabled={!promotable || starting || (emergency && !reason)}>
                    {starting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    {emergency ? "Mettre en prod en urgence" : view.canAdminister ? "Mettre en prod" : "Demander la mise en prod"}
                  </Button>
                  {view.canAdminister && (
                    <Button variant="ghost" size="sm" onClick={() => setEmergency((v) => !v)}>
                      {emergency ? "Annuler l'urgence" : "Urgence…"}
                    </Button>
                  )}
                  {!promotable && (
                    <span className="text-xs text-muted-foreground">
                      {staging.converged
                        ? "La prod tourne déjà la version de staging."
                        : "Staging doit tourner une seule version, pods prêts."}
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  La mise en prod envoie l&apos;image exacte qui tourne en staging, puis la
                  stratégie de release de la prod (canary ou rolling) prend le relais dans
                  l&apos;onglet Release.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {view.activePromotion && (
        <PromotionCard
          deploymentId={deploymentId}
          promotion={view.activePromotion}
          view={view}
          onChanged={refresh}
        />
      )}

      <StagingSettingsCard
        deploymentId={deploymentId}
        staging={view.staging}
        canAdminister={view.canAdminister}
        onChanged={refresh}
      />

      {view.staging?.state === "ready" && (
        <>
          <RunsCard deploymentId={deploymentId} view={view} runs={runs} onChanged={refresh} />
          <PromotionPolicyCard
            // The view is polled: remount only when the saved policy changes,
            // so an edit in progress is not overwritten every few seconds.
            key={view.policy.updatedAt ?? "default"}
            deploymentId={deploymentId}
            policy={view.policy}
            canAdminister={view.canAdminister}
            onSaved={refresh}
          />
          <HistoryCard deploymentId={deploymentId} view={view} promotions={promotions} onChanged={refresh} />
        </>
      )}
    </div>
  );
}
