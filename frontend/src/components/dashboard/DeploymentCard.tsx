"use client";

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
import { useDeploymentPolling } from "@/lib/hooks";
import type { Deployment } from "@/types";
import { useState } from "react";

const ACTIVE_STATUSES = new Set([
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
]);

const HEALTH_COLORS: Record<string, string> = {
  healthy: "bg-green-500",
  degraded: "bg-yellow-500",
  unknown: "bg-gray-400",
  error: "bg-red-500",
  not_deployed: "bg-gray-300",
};

interface Props {
  deployment: Deployment;
  onDelete: (id: number) => void;
}

export function DeploymentCard({ deployment: initial, onDelete }: Props) {
  const isActive = ACTIVE_STATUSES.has(initial.status);
  // Poll only while the deployment is in a transient state
  const polled = useDeploymentPolling(isActive ? initial.id : null);
  const deployment = polled ?? initial;

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmStep, setConfirmStep] = useState(1);

  const handleDeleteClick = () => {
    setConfirmStep(1);
    setConfirmOpen(true);
  };

  const handleConfirm = () => {
    if (confirmStep === 1) {
      setConfirmStep(2);
    } else {
      setConfirmOpen(false);
      onDelete(deployment.id);
    }
  };

  const healthDot =
    HEALTH_COLORS[
      deployment.status === "running"
        ? "healthy"
        : deployment.status === "degraded"
          ? "degraded"
          : deployment.status === "failed"
            ? "error"
            : "unknown"
    ];

  // Parse Terraform outputs
  const outputs = deployment.terraform_outputs
    ? (() => {
        try {
          return JSON.parse(deployment.terraform_outputs);
        } catch {
          return {};
        }
      })()
    : {};

  return (
    <>
      <Card className="flex flex-col">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <span
                className={`inline-block h-2.5 w-2.5 rounded-full ${healthDot}`}
                title={deployment.status}
              />
              {deployment.template_icon && (
                <span className="text-lg">{deployment.template_icon}</span>
              )}
              {deployment.name}
            </CardTitle>
            <Badge variant="outline" className="text-xs capitalize">
              {deployment.template_name || deployment.template_id}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="space-y-3 text-sm">
          <DeploymentStepper deployment={deployment} />

          {deployment.status === "running" &&
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
                {deployment.resource_count !== null && (
                  <div className="mt-2 pt-2 border-t border-border">
                    <span className="text-muted-foreground">Resources: </span>
                    {deployment.resource_count}
                  </div>
                )}
              </div>
            )}
        </CardContent>

        <CardFooter className="mt-auto pt-2">
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto"
            onClick={handleDeleteClick}
            disabled={["deleting", "deleted", "pending"].includes(
              deployment.status,
            )}
          >
            Delete
          </Button>
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
                ? `This will destroy all cloud resources for "${deployment.name}" using Terraform. This cannot be undone.`
                : `All resources (${deployment.resource_count || "unknown"} resources) for "${deployment.name}" will be permanently destroyed. Click confirm to proceed.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleConfirm}>
              {confirmStep === 1
                ? "Yes, delete"
                : "Confirm — destroy everything"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
