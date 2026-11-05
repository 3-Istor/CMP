"use client";

import { intlLocale, type Translate } from "@/components/finops/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { NotConnected } from "@/components/clusters/NotConnected";
import { abortRelease, isNotConnected, promoteRelease, skipReleaseStep } from "@/lib/api";
import { useCan, useRelease, useReleaseHistory } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { ReleaseEvent, ReleaseRevision, ReleaseView } from "@/types";
import { AlertTriangle, ArrowRight, CheckCircle2, Rocket, XCircle } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { DemoTrafficCard } from "./DemoTrafficCard";
import { ReleaseHistoryTable } from "./ReleaseHistoryTable";
import { ReleaseSettingsCard } from "./ReleaseSettingsCard";
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
  outcomeLabel,
  PHASE_TONE,
  TONE,
} from "./format";

type Confirming = null | "full" | "abort";

function releaseName(revision: number | null | undefined, t: Translate): string {
  return revision != null ? t("name.numbered", { revision }) : t("name.plain");
}

function DisabledCard() {
  const t = useTranslations("release");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Rocket className="h-4 w-4 text-muted-foreground" />
          {t("disabled.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-muted-foreground">
        <p>
          {t.rich("disabled.rolling", {
            code: (chunks) => <code className="font-mono">{chunks}</code>,
          })}
        </p>
        <p>{t("disabled.synced")}</p>
      </CardContent>
    </Card>
  );
}

function Banner({ view }: { view: ReleaseView }) {
  const t = useTranslations("release");
  const locale = intlLocale(useLocale());
  const release = releaseName(view.revision, t);
  const stable = imageTag(view.stableImage);

  if (view.phase === "Aborted" || view.phase === "Degraded") {
    const failedStep = view.steps.find((s) => s.status === "failed");
    const failedMetrics =
      failedStep?.metrics.filter((m) => m.phase === "Failed" || m.phase === "Error") ?? [];
    const title = failedStep
      ? t("banner.canaryRejected", { release })
      : view.phase === "Aborted"
        ? t("banner.aborted", { release })
        : t("banner.degraded", { release });
    return (
      <div role="alert" className={cn("flex gap-3 rounded-xl border p-4", TONE.bad)}>
        <XCircle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <div className="space-y-1 text-sm">
          <p className="font-semibold">{title}</p>
          {failedMetrics.map((m) => {
            const value = formatMetricValue(m.value, m.unit, t, locale);
            return (
              <p key={m.name}>
                {m.threshold != null
                  ? t("banner.metricReachedThreshold", {
                      name: m.name,
                      value,
                      threshold: formatMetricValue(m.threshold, m.unit, t, locale),
                    })
                  : t("banner.metricReached", { name: m.name, value })}
              </p>
            );
          })}
          <p className="text-foreground/80">{t("banner.stableServes", { stable })}</p>
        </div>
      </div>
    );
  }

  if (view.phase === "Healthy" && view.forced) {
    return (
      <div role="status" className={cn("flex gap-3 rounded-xl border p-4", TONE.warn)}>
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <div className="space-y-1 text-sm">
          <p className="font-semibold">{t("banner.forced", { release })}</p>
          <p className="text-foreground/80">{t("banner.forcedBody", { stable })}</p>
        </div>
      </div>
    );
  }

  if (view.phase === "Healthy" && view.steps.some(isVisibleStep)) {
    return (
      <div role="status" className={cn("flex gap-3 rounded-xl border p-4", TONE.ok)}>
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <p className="text-sm">
          <span className="font-semibold">{t("banner.promoted", { release })}</span>
          <span className="text-foreground/80"> · {t("banner.stableIs", { stable })}</span>
        </p>
      </div>
    );
  }

  return null;
}

function TrafficSplit({ view }: { view: ReleaseView }) {
  const t = useTranslations("release");
  const canary = view.weights?.canary ?? 0;
  const stable = view.weights?.stable ?? 100 - canary;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t("split.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          role="img"
          aria-label={t("split.ariaLabel", { stable, canary })}
          className="flex h-7 overflow-hidden rounded-md bg-muted font-mono text-xs font-medium"
        >
          {stable > 0 && (
            <div
              className="flex items-center justify-center bg-primary text-primary-foreground transition-[width] duration-500"
              style={{ width: `${stable}%` }}
            >
              {stable >= 10 && t("split.percent", { value: stable })}
            </div>
          )}
          {canary > 0 && (
            <div
              className="flex items-center justify-center bg-amber-500 text-amber-950 transition-[width] duration-500"
              style={{ width: `${canary}%` }}
            >
              {canary >= 10 && t("split.percent", { value: canary })}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <i className="size-2.5 rounded-sm bg-primary" aria-hidden />
            {t("split.stableTag", { tag: imageTag(view.stableImage) })}
          </span>
          {view.canaryHash && view.canaryHash !== view.stableHash && (
            <span className="flex items-center gap-1.5">
              <i className="size-2.5 rounded-sm bg-amber-500" aria-hidden />
              {t("split.canaryTag", { tag: imageTag(view.canaryImage) })}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Pods({ ready, desired }: { ready: number; desired: number }) {
  const t = useTranslations("release");
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
        <span className="sr-only"> {t("pods.ready")}</span>
      </span>
    </span>
  );
}

function Revisions({ revisions }: { revisions: ReleaseRevision[] }) {
  const t = useTranslations("release");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t("revisions.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {revisions.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("revisions.empty")}</p>
        )}
        {revisions.map((r) => (
          <div key={r.hash} className="flex items-center gap-2 text-sm">
            <Badge
              variant="outline"
              className={r.role === "canary" ? TONE.warn : TONE.info}
            >
              {t(`role.${r.role}`)}
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
  const t = useTranslations("release");
  const locale = intlLocale(useLocale());
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t("activity.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        {!event ? (
          <p className="text-sm text-muted-foreground">{t("activity.empty")}</p>
        ) : (
          <ul className="space-y-2 text-sm">
            <li className="flex gap-3">
              <time className="w-11 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                {formatTime(event.started_at, locale)}
              </time>
              <span>{t("activity.started", { revision: event.revision })}</span>
            </li>
            {[...(event.actors ?? [])]
              .sort((a, b) => a.at.localeCompare(b.at))
              .map((a, i) => (
                <li key={`${a.at}-${i}`} className="flex gap-3">
                  <time className="w-11 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                    {formatTime(a.at, locale)}
                  </time>
                  <span>
                    <span className="font-medium">{a.user}</span> {actionLabel(a.action, t)}
                    {a.reason && (
                      <span className="text-muted-foreground">
                        {" · "}
                        {t("activity.reason", { reason: a.reason })}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            {event.finished_at && (
              <li className="flex gap-3">
                <time className="w-11 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                  {formatTime(event.finished_at, locale)}
                </time>
                <span>{outcomeLabel(event.outcome, t)}</span>
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
  const t = useTranslations("release");
  const locale = intlLocale(useLocale());
  const { view, error, live, refresh } = useRelease(deploymentId);
  const {
    events,
    loading: historyLoading,
    refresh: refreshHistory,
  } = useReleaseHistory(deploymentId);
  const { can } = useCan(projectName);
  const canOperate = can("release.operate");
  const canReadSettings = can("app.viewConfig");

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
            t("toast.promotedAll"),
          )
        : await run(
            () => abortRelease(deploymentId, trimmed || null),
            t("toast.aborted"),
          );
    if (ok) closeConfirm();
  };

  if (!view) {
    const nc = isNotConnected(error);
    if (nc) return <NotConnected cloud={nc.cloud} />;
    if (error) {
      return (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            {t("errors.readState", { error: errorMessage(new Error(error)) })}
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
        {canReadSettings && (
          <ReleaseSettingsCard deploymentId={deploymentId} inProgress={false} />
        )}
        {events.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("history.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ReleaseHistoryTable events={events} />
            </CardContent>
          </Card>
        )}
      </div>
    );
  }

  const hasCanary = Boolean(view.canaryHash && view.canaryHash !== view.stableHash);
  const inProgress =
    hasCanary && (view.phase === "Progressing" || view.phase === "Paused");
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
  // A fullstack canary keeps its backend rolling, with the same revision numbers.
  const canaryEvents = events.filter((e) => e.strategy !== "rolling");
  const currentEvent =
    canaryEvents.find((e) => e.revision === view.revision) ??
    canaryEvents.find((e) => e.outcome === "running");
  const eta =
    view.etaSeconds != null
      ? t("eta.approx", { duration: formatDuration(view.etaSeconds, t) })
      : view.phase === "Paused"
        ? t("eta.afterApproval")
        : "-";
  const release = releaseName(view.revision, t);

  return (
    <div className="space-y-6">
      <Banner view={view} />

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="mr-1 text-lg font-semibold">{release}</h2>
            {view.phase && (
              <Badge variant="outline" className={TONE[PHASE_TONE[view.phase]]}>
                {t(`phase.${view.phase}`)}
              </Badge>
            )}
            {view.forced && (
              <Badge variant="outline" className={TONE.warn}>
                {t("badges.forced")}
              </Badge>
            )}
            {view.strategy && (
              <Badge variant="secondary">
                {t.has(`strategyNames.${view.strategy}`) ? t(`strategyNames.${view.strategy}`) : view.strategy}
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
                ? t("live.stream")
                : live === "polling"
                  ? t("live.polling")
                  : t("live.connecting")}
            </span>
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <div className="col-span-2 min-w-0">
              <dt className="text-xs text-muted-foreground">{t("fields.version")}</dt>
              <dd className="flex items-center gap-1.5 truncate font-mono">
                {hasCanary ? (
                  <>
                    <span title={view.stableImage ?? undefined}>
                      {imageTag(view.stableImage)}
                    </span>
                    <ArrowRight
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                      aria-label={t("fields.toward")}
                    />
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
              <dt className="text-xs text-muted-foreground">{t("fields.step")}</dt>
              <dd className="font-mono tabular-nums">
                {stepCurrent != null ? `${stepCurrent}/${stepTotal}` : "-"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">{t("fields.eta")}</dt>
              <dd>{eta}</dd>
            </div>
            {view.startedAt && (
              <div className="col-span-2 sm:col-span-4">
                <dt className="sr-only">{t("fields.startedSr")}</dt>
                <dd className="text-xs text-muted-foreground">
                  {t("fields.startedOn", { date: formatDateTime(view.startedAt, locale) })}
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
                    {t("actions.promoteAll")}
                  </Button>
                )}
                {view.actions.abort && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setConfirming("abort")}
                    disabled={busy}
                  >
                    {t("actions.abort")}
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
                  ? t("confirm.promoteAllTitle")
                  : t("confirm.abortTitle")}
              </p>
              <p className="text-xs text-foreground/80">
                {confirming === "full"
                  ? t("confirm.promoteAllBody")
                  : t("confirm.abortBody")}
              </p>
              <Label htmlFor={reasonId} className="text-xs text-foreground">
                {confirming === "full"
                  ? t("confirm.reason")
                  : t("confirm.reasonOptional")}
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
                  {t("confirm.submit")}
                </Button>
                <Button size="sm" variant="ghost" type="button" onClick={closeConfirm}>
                  {t("confirm.cancel")}
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
              {t("steps.title")}
              {view.preset && (
                <span className="text-xs font-normal text-muted-foreground">
                  {t("steps.preset", { preset: view.preset })}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {stepTotal === 0 ? (
              <p className="text-sm text-muted-foreground">{t("steps.empty")}</p>
            ) : (
              <ReleaseStepper
                view={view}
                canOperate={canOperate}
                busy={busy}
                onContinue={() =>
                  run(() => promoteRelease(deploymentId, "next", null), t("toast.nextStep"))
                }
                onAbort={() => setConfirming("abort")}
                onSkip={(skipReason) =>
                  run(() => skipReleaseStep(deploymentId, skipReason), t("toast.stepSkipped"))
                }
              />
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <TrafficSplit view={view} />
          <DemoTrafficCard
            deploymentId={deploymentId}
            canOperate={canOperate}
            weights={view.weights}
          />
          <Revisions revisions={view.revisions} />
          <Activity event={currentEvent} />
        </div>
      </div>

      {canReadSettings && (
        <ReleaseSettingsCard deploymentId={deploymentId} inProgress={inProgress} />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{t("history.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <ReleaseHistoryTable events={canaryEvents} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
