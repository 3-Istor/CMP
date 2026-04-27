"use client";

import { DeploymentHealthPanel } from "@/components/dashboard/DeploymentHealthPanel";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAppHealth, useDeploymentPolling } from "@/lib/hooks";
import type { Deployment } from "@/types";
import { Activity } from "lucide-react";
import { useState } from "react";

const ACTIVE_STATUSES = new Set([
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
]);

interface Props {
  deployment: Deployment;
  onDelete: (id: number) => void;
}

export function DeploymentCard({ deployment, onDelete }: Props) {
  const isActive = ACTIVE_STATUSES.has(deployment.status);
  // Poll only while the deployment is in a transient state
  const polled = useDeploymentPolling(isActive ? deployment.id : null);
  // Use polled data if available (during active deployment), otherwise use parent data
  const currentDeployment = polled ?? deployment;

  // Fetch real health status for running/degraded deployments
  const shouldFetchHealth = ["running", "degraded"].includes(
    currentDeployment.status,
  );
  const { health } = useAppHealth(
    shouldFetchHealth ? currentDeployment.id : null,
    10000, // Poll every 10 seconds
  );

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmStep, setConfirmStep] = useState(1);
  const [healthOpen, setHealthOpen] = useState(false);

  const handleDeleteClick = () => {
    setConfirmStep(1);
    setConfirmOpen(true);
  };

  const handleConfirm = () => {
    if (confirmStep === 1) {
      setConfirmStep(2);
    } else {
      setConfirmOpen(false);
      onDelete(currentDeployment.id);
    }
  };

  // Get health badge info based on REAL health check
  const getHealthBadge = () => {
    // For running/degraded deployments, use actual health check
    if (["running", "degraded"].includes(currentDeployment.status)) {
      if (!health) {
        return { label: "Checking...", variant: "outline" as const };
      }

      if (health.status === "healthy") {
        return { label: "Healthy", variant: "default" as const };
      }
      if (health.status === "degraded") {
        return { label: "Degraded", variant: "secondary" as const };
      }
      if (health.status === "down") {
        return { label: "Down", variant: "destructive" as const };
      }
      return { label: "Unknown", variant: "outline" as const };
    }

    // For failed deployments
    if (currentDeployment.status === "failed") {
      return { label: "Failed", variant: "destructive" as const };
    }

    return null;
  };

  // Get progress badge info
  const getProgressBadge = () => {
    if (currentDeployment.status === "pending") {
      return { label: "Queued", variant: "outline" as const };
    }
    if (
      ["initializing", "planning", "deploying"].includes(
        currentDeployment.status,
      )
    ) {
      return { label: "Deploying", variant: "default" as const };
    }
    if (currentDeployment.status === "deleting") {
      return { label: "Deleting", variant: "destructive" as const };
    }
    if (currentDeployment.status === "deleted") {
      return { label: "Deleted", variant: "outline" as const };
    }
    return null;
  };

  const healthBadge = getHealthBadge();
  const progressBadge = getProgressBadge();

  // Parse Terraform outputs
  const outputs = currentDeployment.terraform_outputs
    ? (() => {
        try {
          return JSON.parse(currentDeployment.terraform_outputs);
        } catch {
          return {};
        }
      })()
    : {};

  return (
    <>
      <Card className="flex flex-col">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-base flex items-center gap-2">
              {currentDeployment.template_icon && (
                <span className="text-lg">
                  {currentDeployment.template_icon}
                </span>
              )}
              {currentDeployment.name}
            </CardTitle>
            <div className="flex items-center gap-2">
              {healthBadge && (
                <Badge
                  variant={healthBadge.variant}
                  className={`text-xs ${healthBadge.label === "Degraded" ? "bg-orange-500 text-white hover:bg-orange-600" : ""}`}
                >
                  {healthBadge.label}
                </Badge>
              )}
              {progressBadge && (
                <Badge variant={progressBadge.variant} className="text-xs">
                  {progressBadge.label}
                </Badge>
              )}
              <Badge variant="outline" className="text-xs capitalize">
                {currentDeployment.template_name ||
                  currentDeployment.template_id}
              </Badge>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-3 text-sm">
          <DeploymentStepper deployment={currentDeployment} />

          {currentDeployment.status === "running" &&
            Object.keys(outputs).length > 0 && (
              <div className="rounded-md bg-muted p-3 space-y-1 text-xs font-mono">
                <div className="font-semibold text-muted-foreground mb-2">
                  Outputs:
                </div>
                {Object.entries(outputs).map(([key, value]) => (
                  <div key={key}>
                    <span className="text-muted-foreground">{key}: </span>
                    {typeof value === "string" && value.startsWith("http") ? (
                      <a
                        href={value}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline"
                      >
                        {value}
                      </a>
                    ) : (
                      <span>{String(value)}</span>
                    )}
                  </div>
                ))}
                {/* {currentDeployment.resource_count !== null && (
                  <div className="mt-2 pt-2 border-t border-border">
                    <span className="text-muted-foreground">Resources: </span>
                    {currentDeployment.resource_count}
                  </div>
                )} */}
              </div>
            )}
        </CardContent>

        <CardFooter className="mt-auto pt-2">
          <div className="flex gap-2 ml-auto">
            {["running", "degraded"].includes(currentDeployment.status) && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setHealthOpen(true)}
              >
                <Activity className="h-4 w-4 mr-1.5" />
                Health Details
              </Button>
            )}
            <Button
              variant="destructive"
              size="sm"
              onClick={handleDeleteClick}
              disabled={["deleting", "deleted", "pending"].includes(
                currentDeployment.status,
              )}
            >
              Delete
            </Button>
          </div>
        </CardFooter>
      </Card>

      {/* Double-confirmation dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirmStep === 1
                ? "Delete deployment?"
                : "⚠️ Are you absolutely sure?"}
            </DialogTitle>
            <DialogDescription>
              {confirmStep === 1
                ? `This will destroy all cloud resources for "${currentDeployment.name}" using Terraform. This cannot be undone.`
                : `All resources (${currentDeployment.resource_count || "unknown"} resources) for "${currentDeployment.name}" will be permanently destroyed. Click confirm to proceed.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleConfirm}>
              {confirmStep === 1
                ? "Yes, delete"
                : "Confirm - destroy everything"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Health Details Dialog */}
      <DeploymentHealthPanel
        deploymentId={currentDeployment.id}
        deploymentName={currentDeployment.name}
        open={healthOpen}
        onClose={() => setHealthOpen(false)}
      />
    </>
  );
}
