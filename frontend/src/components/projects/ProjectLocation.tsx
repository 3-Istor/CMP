"use client";

import { errorMessage } from "@/components/activity/labels";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Label } from "@/components/ui/label";
import { type LastWal, type ProjectMove, getLastWal, getMoveDataLoss, moveAction, startMove } from "@/lib/api";
import { useGlobalHealth } from "@/lib/hooks";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { CircleAlert, CircleCheck, Circle, Loader2, SkipForward } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

const cloudName = (cloud: string) => (cloud === "onprem" ? "On-prem" : cloud.toUpperCase());

type MoveTarget = "onprem" | "public";

function StepIcon({ status }: { status: string }) {
  if (status === "done") return <CircleCheck className="h-4 w-4 text-emerald-600" aria-hidden />;
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin" aria-hidden />;
  if (status === "skipped") return <SkipForward className="h-4 w-4 text-muted-foreground" aria-hidden />;
  if (status === "failed") return <CircleAlert className="h-4 w-4 text-destructive" aria-hidden />;
  return <Circle className="h-4 w-4 text-muted-foreground" aria-hidden />;
}

const time = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

/** Banner for the project overview while a move is active. */
export function MoveBanner() {
  const t = useTranslations("projectSettings.location");
  const { projectName, move } = useProjectContext();
  if (!move) return null;
  const current = move.steps.find((s) => s.status !== "done" && s.status !== "skipped") ?? move.steps[move.steps.length - 1];
  const cloud = cloudName(move.target);
  return (
    <div role="status" className="mb-6 flex items-center justify-between gap-4 rounded-lg border px-4 py-3 text-sm">
      <span className="flex items-center gap-2">
        {move.status === "failed" ? (
          <CircleAlert className="h-4 w-4 text-destructive" aria-hidden />
        ) : (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        )}
        {move.status === "failed"
          ? t("bannerFailed", { cloud })
          : move.status === "cancelling"
            ? t("cancelling")
            : t("banner", { cloud, step: t(`steps.${current.name}`) })}
      </span>
      <span className="flex items-center gap-3">
        <MoveActions />
        <Link
          href={`/projects/${encodeURIComponent(projectName)}/settings`}
          className="font-medium text-primary hover:underline"
        >
          {t("details")}
        </Link>
      </span>
    </div>
  );
}

/** Start-move buttons and their confirmation dialog; nothing while a move is active or without permission. */
export function MoveButtons() {
  const t = useTranslations("projectSettings.location");
  const { projectName, targetCloud, can, move, setMove } = useProjectContext();
  const { health } = useGlobalHealth(0);
  const [confirming, setConfirming] = useState<MoveTarget | null>(null);
  const [dataLoss, setDataLoss] = useState(false);
  const [lost, setLost] = useState<string[]>([]);
  const [wal, setWal] = useState<LastWal[]>([]);
  const isAdmin = usePlatformAdmin();

  if (!targetCloud || move || !can("project.move")) return null;

  const up = (cloud: string) => health?.clusters.some((c) => c.cloud === cloud && c.state === "up") ?? false;
  // On-prem is not in the cluster health list; only a public source can be reported not up.
  const forced = isAdmin && targetCloud !== "onprem" && !!health && !up(targetCloud);
  // From a public cloud the "public" target is the other one; from on-prem, any public cloud that is up.
  const other = targetCloud === "aws" ? "gcp" : "aws";
  const options: { target: MoveTarget; label: string; enabled: boolean; down?: string }[] =
    targetCloud === "onprem"
      ? [
          {
            target: "public",
            label: t("movePublic"),
            enabled: up("aws") || up("gcp"),
            down: t("publicName"),
          },
        ]
      : [
          { target: "onprem", label: t("moveOnprem"), enabled: true },
          { target: "public", label: t("move", { cloud: cloudName(other) }), enabled: up(other), down: cloudName(other) },
        ];
  const confirmName = confirming === "onprem" ? cloudName("onprem") : targetCloud === "onprem" ? t("publicName") : cloudName(other);

  const start = async () => {
    // The typed project name is the data-loss confirmation of a forced move.
    setMove(await startMove(projectName, dataLoss || forced, confirming ?? "public", forced));
  };

  return (
    <>
      <div className="flex items-center gap-3">
        {options.map((o) => (
          <Button
            key={o.target}
            variant="outline"
            disabled={!o.enabled}
            title={health && !o.enabled ? t("notUp", { cloud: o.down ?? "" }) : undefined}
            onClick={() => {
              setDataLoss(false);
              setLost([]);
              setConfirming(o.target);
              // On failure the move request itself names what would be lost.
              getMoveDataLoss(projectName)
                .then(setLost)
                .catch(() => setLost([]));
              setWal([]);
              if (forced) getLastWal(projectName).then(setWal).catch(() => setWal([]));
            }}
          >
            {forced ? t("force", { cloud: o.target === "onprem" ? cloudName("onprem") : (o.down ?? "") }) : o.label}
          </Button>
        ))}
      </div>
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => !open && setConfirming(null)}
        level={2}
        title={t(forced ? "forceTitle" : "confirmTitle", { name: projectName, cloud: confirmName })}
        description={
          forced
            ? t("forceDescription", { source: cloudName(targetCloud) })
            : t("confirmDescription", { cloud: confirmName })
        }
        destructive={forced}
        expectedName={projectName}
        confirmLabel={t("confirmLabel")}
        onConfirm={start}
      >
        {forced &&
          wal.map((w) => (
            <p key={w.database} className="text-sm text-destructive">
              {w.last_wal_at
                ? t("lastWal", { time: new Date(w.last_wal_at).toLocaleString(), database: w.database })
                : t(w.reason ? "lastWalUnknown" : "lastWalNone", { database: w.database })}
            </p>
          ))}
        {lost.length > 0 && (
          <div className="flex items-center gap-2">
            <input
              id="move-data-loss"
              type="checkbox"
              checked={dataLoss}
              onChange={(e) => setDataLoss(e.target.checked)}
            />
            <Label htmlFor="move-data-loss">{t("dataLoss", { names: lost.join(", ") })}</Label>
          </div>
        )}
      </ConfirmDialog>
    </>
  );
}

/** Retry / Cancel of the current move; none while it is cancelling. */
export function MoveActions() {
  const t = useTranslations("projectSettings.location");
  const { projectName, can, move, setMove } = useProjectContext();
  const [busy, setBusy] = useState(false);
  if (!move || !can("project.move")) return null;

  const act = (action: "retry" | "cancel") => {
    setBusy(true);
    moveAction(projectName, move.id, action)
      .then((m: ProjectMove) => setMove(m))
      .catch((err) => toast.error(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const canCancel = move.status !== "cancelling" && move.steps.find((s) => s.name === "stop_source")?.status === "pending";
  if (move.status !== "failed" && !canCancel) return null;
  return (
    <div className="flex gap-2">
      {move.status === "failed" && (
        <Button size="sm" disabled={busy} onClick={() => act("retry")}>
          {t("retry")}
        </Button>
      )}
      {canCancel && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => act("cancel")}>
          {t("cancel")}
        </Button>
      )}
    </div>
  );
}

/** Steps of the current move with Retry / Cancel; nothing without a move. */
export function MoveProgress() {
  const t = useTranslations("projectSettings.location");
  const { move } = useProjectContext();
  if (!move) return null;
  return (
    <div className="space-y-3 border-t pt-4">
      <p className="text-sm font-medium">
        {move.status === "cancelling" ? t("cancelling") : t("moving", { cloud: cloudName(move.target) })}
      </p>
      <ol className="space-y-1.5 text-sm">
        {move.steps.map((s) => (
          <li key={s.name} className="flex items-center gap-2">
            <StepIcon status={s.status} />
            <span>{t(`steps.${s.name}`)}</span>
            {s.status === "skipped" && (
              <span className="text-xs text-muted-foreground">{s.detail ?? t("skipped")}</span>
            )}
            <span className="ml-auto font-mono text-xs text-muted-foreground">
              {time(s.started_at)}
              {s.finished_at ? ` - ${time(s.finished_at)}` : ""}
            </span>
          </li>
        ))}
      </ol>
      {move.detail && <p className="text-sm text-destructive">{move.detail}</p>}
      <MoveActions />
    </div>
  );
}

export function ProjectLocation() {
  const t = useTranslations("projectSettings.location");
  const { targetCloud } = useProjectContext();
  if (!targetCloud) return null;

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            {t("runsOn", { cloud: cloudName(targetCloud) })}
            <Badge variant="outline" className="text-xs">
              {cloudName(targetCloud)}
            </Badge>
          </div>
          <MoveButtons />
        </div>

        <MoveProgress />
      </CardContent>
    </Card>
  );
}
