"use client";

import { ClusterLogsDialog } from "@/components/dashboard/ClusterLogsDialog";
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
import { ApiError, startCluster, stopCluster } from "@/lib/api";
import type { ClusterDownPreview, ClusterStatus } from "@/types";
import { useState } from "react";
import { toast } from "sonner";

const LABELS = { aws: "EKS", gcp: "GKE" };
const STEPS = {
  up: ["init", "apply", "kubeconfig", "nodes", "argocd", "wireguard"],
  down: ["checks", "destroy"],
};
// The step whose progress is reported as done/total.
const COUNTED = { up: "apply", down: "destroy" };

function badgeProps(state: ClusterStatus["state"]) {
  switch (state) {
    case "up":
      return { variant: "default" as const, className: "" };
    case "down":
      return { variant: "secondary" as const, className: "text-muted-foreground" };
    case "error":
      return { variant: "destructive" as const, className: "" };
    default:
      return {
        variant: "secondary" as const,
        className: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
      };
  }
}

// API errors carry the raw body, usually {"detail": "..."}.
function errorDetail(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const d = JSON.parse(err.body)?.detail;
      if (typeof d === "string") return d;
    } catch {}
  }
  return err instanceof Error ? err.message : "Request failed";
}

function StepBar({ cluster }: { cluster: ClusterStatus }) {
  const steps = STEPS[cluster.operation ?? "up"];
  const current = cluster.step ? steps.indexOf(cluster.step) : -1;
  const counted = COUNTED[cluster.operation ?? "up"];
  return (
    <div className="space-y-1">
      <div className="flex gap-1">
        {steps.map((s, i) => {
          const pct =
            i < current
              ? 100
              : i === current
                ? s === counted && cluster.total > 0
                  ? Math.min(100, (cluster.done / cluster.total) * 100)
                  : 50
                : 0;
          return (
            <div key={s} className="h-1.5 flex-1 rounded bg-muted overflow-hidden">
              <div className="h-full bg-amber-500" style={{ width: `${pct}%` }} />
            </div>
          );
        })}
      </div>
      <div className="text-[10px] text-muted-foreground">
        {cluster.step ?? "starting"}
        {cluster.step === counted && cluster.total > 0
          ? ` (${cluster.done}/${cluster.total})`
          : ""}
      </div>
    </div>
  );
}

export function ClusterCard({
  cluster,
  canOperate,
  onChanged,
}: {
  cluster: ClusterStatus;
  canOperate: boolean;
  onChanged: () => void;
}) {
  const name = LABELS[cluster.cloud];
  const [busy, setBusy] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [preview, setPreview] = useState<ClusterDownPreview | null>(null);
  const moving = cluster.state === "starting" || cluster.state === "stopping";
  const badge = badgeProps(cluster.state);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setBusy(false);
    }
  };

  const up = () => run(() => startCluster(cluster.cloud));
  const askDown = () =>
    run(async () => setPreview(await stopCluster(cluster.cloud, false)));
  const confirmDown = () =>
    run(async () => {
      await stopCluster(cluster.cloud, true);
      setPreview(null);
    });

  return (
    <div className="space-y-2 text-xs bg-muted/50 rounded px-2 py-1.5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px]">{name}</span>
        <Badge variant={badge.variant} className={`text-[10px] h-4 px-1.5 ${badge.className}`}>
          {cluster.state}
        </Badge>
      </div>
      {cluster.state === "up" && cluster.wireguard_running !== null && (
        <div className="text-[10px] text-muted-foreground">
          WireGuard: {cluster.wireguard_running ? "running" : "not running"}
        </div>
      )}
      {cluster.state === "error" && cluster.last_error && (
        <div className="text-[10px] text-destructive break-words">
          {cluster.last_error}
        </div>
      )}
      {moving && <StepBar cluster={cluster} />}
      <div className="flex gap-1.5">
        {canOperate && (cluster.state === "down" || cluster.state === "error") && (
          <Button size="xs" disabled={busy} onClick={up}>
            UP
          </Button>
        )}
        {canOperate && cluster.state === "up" && (
          <Button size="xs" variant="destructive" disabled={busy} onClick={askDown}>
            DOWN
          </Button>
        )}
        {(moving || cluster.state === "error") && (
          <Button size="xs" variant="outline" onClick={() => setLogsOpen(true)}>
            Logs
          </Button>
        )}
      </div>

      <ClusterLogsDialog
        cloud={cluster.cloud}
        label={name}
        open={logsOpen}
        onOpenChange={setLogsOpen}
      />
      <Dialog open={preview !== null} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Stop {name}?</DialogTitle>
            <DialogDescription>
              {preview?.affected_apps.length
                ? "These apps run on this cluster and will go down:"
                : "No app runs on this cluster."}
            </DialogDescription>
          </DialogHeader>
          {!!preview?.affected_apps.length && (
            <ul className="text-sm font-mono max-h-48 overflow-auto">
              {preview.affected_apps.map((a) => (
                <li key={a.deployment_id}>
                  {a.project} / {a.app}
                </li>
              ))}
            </ul>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreview(null)}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={busy} onClick={confirmDown}>
              Stop cluster
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
