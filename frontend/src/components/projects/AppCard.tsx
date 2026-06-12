"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { Deployment } from "@/types";
import { CheckCircle2, Clock, GitBranch, Loader2, XCircle } from "lucide-react";
import Link from "next/link";

interface Props {
  app: Deployment;
  projectName: string;
}

const ACTIVE_STATUSES = new Set([
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
]);

const STATUS_PROGRESS: Record<Deployment["status"], number> = {
  pending: 5,
  initializing: 20,
  planning: 40,
  deploying: 70,
  running: 100,
  degraded: 100,
  failed: 100,
  deleting: 50,
  deleted: 100,
};

const STATUS_LABEL: Record<Deployment["status"], string> = {
  pending: "Queued",
  initializing: "Initializing",
  planning: "Planning",
  deploying: "Deploying",
  running: "Running",
  degraded: "Degraded",
  failed: "Failed",
  deleting: "Deleting",
  deleted: "Deleted",
};

function StatusIcon({ status }: { status: Deployment["status"] }) {
  if (status === "running")
    return <CheckCircle2 className="h-4 w-4 text-green-500" />;
  if (status === "failed" || status === "deleted")
    return <XCircle className="h-4 w-4 text-destructive" />;
  if (ACTIVE_STATUSES.has(status))
    return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  return <Clock className="h-4 w-4 text-muted-foreground" />;
}

const STATUS_VARIANT: Record<
  Deployment["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  running: "default",
  degraded: "secondary",
  pending: "outline",
  initializing: "outline",
  planning: "outline",
  deploying: "outline",
  failed: "destructive",
  deleting: "secondary",
  deleted: "secondary",
};

export function AppCard({ app, projectName }: Props) {
  const isActive = ACTIVE_STATUSES.has(app.status);
  const progress = STATUS_PROGRESS[app.status] ?? 0;

  return (
    <Link
      href={`/projects/${projectName}/apps/${app.id}`}
      className="group block"
    >
      <Card className="h-full transition-shadow group-hover:shadow-md group-hover:border-primary/30">
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              {app.template_icon && (
                <span className="text-2xl shrink-0">{app.template_icon}</span>
              )}
              <CardTitle className="text-base leading-tight truncate">
                {app.name}
              </CardTitle>
            </div>
            <Badge
              variant={STATUS_VARIANT[app.status]}
              className="shrink-0 capitalize gap-1"
            >
              <StatusIcon status={app.status} />
              {STATUS_LABEL[app.status]}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="space-y-2 pt-0">
          <p className="text-xs text-muted-foreground">
            {app.template_name || app.template_id}
          </p>

          {/* ── Progress bar (only during active deployments) ── */}
          {isActive && (
            <div className="space-y-1.5 pt-0.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="animate-pulse">{app.step_message}</span>
                <span className="tabular-nums font-mono">{progress}%</span>
              </div>
              <Progress
                value={progress}
                className="h-1.5"
              />
            </div>
          )}

          {/* ── Static step message for non-active states ── */}
          {!isActive && app.step_message && (
            <p className="text-xs text-muted-foreground line-clamp-1">
              {app.step_message}
            </p>
          )}

          {app.github_repo_url && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <GitBranch className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate font-mono">
                {app.github_repo_url.replace("https://github.com/", "")}
              </span>
            </div>
          )}

          {app.k8s_namespace && (
            <div className="rounded bg-muted/50 px-2 py-0.5 text-xs font-mono text-muted-foreground">
              ns: {app.k8s_namespace}
            </div>
          )}

          <p className="text-xs text-muted-foreground">
            {new Date(app.created_at).toLocaleDateString(undefined, {
              day: "numeric",
              month: "short",
              year: "numeric",
            })}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}
