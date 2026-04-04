"use client";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import type { Deployment, DeploymentStatus } from "@/types";

const STEPS: { status: DeploymentStatus; label: string }[] = [
  { status: "pending", label: "Queued" },
  { status: "deploying_openstack", label: "OpenStack DB VMs" },
  { status: "deploying_aws", label: "AWS ASG + ALB" },
  { status: "running", label: "Running" },
];

const STATUS_PROGRESS: Record<DeploymentStatus, number> = {
  pending: 5,
  deploying_openstack: 30,
  deploying_aws: 65,
  running: 100,
  degraded: 100,
  rolling_back: 50,
  failed: 100,
  deleting: 80,
  deleted: 100,
};

const STATUS_COLOR: Record<DeploymentStatus, string> = {
  pending: "secondary",
  deploying_openstack: "default",
  deploying_aws: "default",
  running: "default",
  degraded: "destructive",
  rolling_back: "secondary",
  failed: "destructive",
  deleting: "secondary",
  deleted: "secondary",
};

interface Props {
  deployment: Deployment;
}

export function DeploymentStepper({ deployment }: Props) {
  const progress = STATUS_PROGRESS[deployment.status] ?? 0;
  const isFailed = deployment.status === "failed";
  const isRollingBack = deployment.status === "rolling_back";

  return (
    <div className="space-y-4">
      {/* Step indicators */}
      <div className="flex items-center gap-2">
        {STEPS.map((step, i) => {
          const stepProgress = STATUS_PROGRESS[step.status];
          const currentProgress = STATUS_PROGRESS[deployment.status] ?? 0;
          const isDone = currentProgress >= stepProgress && !isFailed;
          const isActive = deployment.status === step.status;

          return (
            <div key={step.status} className="flex items-center gap-2">
              <div
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold transition-colors ${
                  isFailed && isActive
                    ? "bg-destructive text-destructive-foreground"
                    : isDone
                      ? "bg-primary text-primary-foreground"
                      : isActive
                        ? "bg-primary/70 text-primary-foreground animate-pulse"
                        : "bg-muted text-muted-foreground"
                }`}
              >
                {isDone && !isActive ? "✓" : i + 1}
              </div>
              <span
                className={`text-xs hidden sm:block ${
                  isActive ? "font-semibold" : "text-muted-foreground"
                }`}
              >
                {step.label}
              </span>
              {i < STEPS.length - 1 && (
                <div
                  className={`h-px w-6 ${isDone ? "bg-primary" : "bg-muted"}`}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Progress bar */}
      <Progress
        value={progress}
        className={isFailed || isRollingBack ? "opacity-50" : ""}
      />

      {/* Status message */}
      <div className="flex items-center gap-2">
        <Badge
          variant={
            STATUS_COLOR[deployment.status] as
              | "default"
              | "secondary"
              | "destructive"
              | "outline"
          }
        >
          {deployment.status.replace(/_/g, " ")}
        </Badge>
        <span className="text-sm text-muted-foreground">
          {deployment.step_message}
        </span>
      </div>
    </div>
  );
}
