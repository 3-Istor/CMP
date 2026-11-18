"use client";

import { errorMessage } from "@/components/activity/labels";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Label } from "@/components/ui/label";
import { type ProjectMove, moveAction, startMove } from "@/lib/api";
import { useCurrentMove, useGlobalHealth } from "@/lib/hooks";
import { CircleAlert, CircleCheck, Circle, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

// Imported apps keep their data outside the database backups.
const BACKING = ["import-service", "import-backing"];

const cloudName = (cloud: string) => cloud.toUpperCase();

function StepIcon({ status }: { status: string }) {
  if (status === "done") return <CircleCheck className="h-4 w-4 text-emerald-600" aria-hidden />;
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin" aria-hidden />;
  if (status === "failed") return <CircleAlert className="h-4 w-4 text-destructive" aria-hidden />;
  return <Circle className="h-4 w-4 text-muted-foreground" aria-hidden />;
}

const time = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

/** Banner for the project overview while a move is active. */
export function MoveBanner() {
  const t = useTranslations("projectSettings.location");
  const { projectName } = useProjectContext();
  const { move } = useCurrentMove(projectName);
  if (!move) return null;
  const current = move.steps.find((s) => s.status !== "done") ?? move.steps[move.steps.length - 1];
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
          : t("banner", { cloud, step: t(`steps.${current.name}`) })}
      </span>
      <Link
        href={`/projects/${encodeURIComponent(projectName)}/settings`}
        className="font-medium text-primary hover:underline"
      >
        {t("details")}
      </Link>
    </div>
  );
}

export function ProjectLocation() {
  const t = useTranslations("projectSettings.location");
  const { projectName, targetCloud, apps, can } = useProjectContext();
  const { health } = useGlobalHealth(0);
  const { move, setMove } = useCurrentMove(projectName);
  const [confirming, setConfirming] = useState(false);
  const [dataLoss, setDataLoss] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!targetCloud) return null;
  if (targetCloud === "onprem") {
    return <p className="text-sm text-muted-foreground">{t("onprem")}</p>;
  }

  const target = targetCloud === "aws" ? "gcp" : "aws";
  const targetName = cloudName(target);
  const targetUp = health?.clusters.some((c) => c.cloud === target && c.state === "up") ?? false;
  const backing = apps.filter((a) => BACKING.includes(a.template_id)).map((a) => a.name);
  const canMove = can("project.move");

  const act = (action: "retry" | "cancel") => {
    if (!move) return;
    setBusy(true);
    moveAction(projectName, move.id, action)
      .then((m: ProjectMove) => setMove(m))
      .catch((err) => toast.error(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const start = async () => {
    setMove(await startMove(projectName, dataLoss));
  };

  return (
    <>
      <Card>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              {t("runsOn", { cloud: cloudName(targetCloud) })}
              <Badge variant="outline" className="text-xs">
                {cloudName(targetCloud)}
              </Badge>
            </div>
            {canMove && !move && (
              <div className="flex items-center gap-3">
                {health && !targetUp && (
                  <span className="text-sm text-muted-foreground">{t("notUp", { cloud: targetName })}</span>
                )}
                <Button
                  variant="outline"
                  disabled={!targetUp}
                  onClick={() => {
                    setDataLoss(false);
                    setConfirming(true);
                  }}
                >
                  {t("move", { cloud: targetName })}
                </Button>
              </div>
            )}
          </div>

          {move && (
            <div className="space-y-3 border-t pt-4">
              <p className="text-sm font-medium">{t("moving", { cloud: cloudName(move.target) })}</p>
              <ol className="space-y-1.5 text-sm">
                {move.steps.map((s) => (
                  <li key={s.name} className="flex items-center gap-2">
                    <StepIcon status={s.status} />
                    <span>{t(`steps.${s.name}`)}</span>
                    <span className="ml-auto font-mono text-xs text-muted-foreground">
                      {time(s.started_at)}
                      {s.finished_at ? ` - ${time(s.finished_at)}` : ""}
                    </span>
                  </li>
                ))}
              </ol>
              {move.detail && <p className="text-sm text-destructive">{move.detail}</p>}
              {canMove && (
                <div className="flex gap-2">
                  {move.status === "failed" && (
                    <Button size="sm" disabled={busy} onClick={() => act("retry")}>
                      {t("retry")}
                    </Button>
                  )}
                  {move.steps.find((s) => s.name === "stop_source")?.status !== "done" && (
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => act("cancel")}>
                      {t("cancel")}
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        level={2}
        title={t("confirmTitle", { name: projectName, cloud: targetName })}
        description={t("confirmDescription", { cloud: targetName })}
        expectedName={projectName}
        confirmLabel={t("confirmLabel")}
        onConfirm={start}
      >
        {backing.length > 0 && (
          <div className="flex items-center gap-2">
            <input
              id="move-data-loss"
              type="checkbox"
              checked={dataLoss}
              onChange={(e) => setDataLoss(e.target.checked)}
            />
            <Label htmlFor="move-data-loss">{t("dataLoss", { names: backing.join(", ") })}</Label>
          </div>
        )}
      </ConfirmDialog>
    </>
  );
}
