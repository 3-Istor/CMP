"use client";

import { AppIcon } from "@/components/app-icon/AppIcon";

import { DeploymentHealthPanel } from "@/components/dashboard/DeploymentHealthPanel";
import { Github } from "@/components/icons/Github";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { appUrlOf } from "@/lib/utils";
import type { Deployment } from "@/types";
import { Activity, AlertTriangle, ExternalLink, FileCode, Globe, Shield } from "lucide-react";
import { useTranslations } from "next-intl";
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
  /**
   * Whether the viewer may delete this app. Defaults to true so callers that
   * render the card without project context keep their current behaviour.
   */
  canDelete?: boolean;
}

export function DeploymentCard({
  deployment,
  onDelete,
  canDelete = true,
}: Props) {
  const t = useTranslations("dashboard");
  const isActive = ACTIVE_STATUSES.has(deployment.status);
  // Poll only while the deployment is in a transient state
  const polled = useDeploymentPolling(isActive ? deployment.id : null);
  // Use polled data if available (during active deployment), otherwise use parent data
  const currentDeployment = polled ?? deployment;
  const appUrl = appUrlOf(currentDeployment.terraform_outputs);

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

  const isKubernetes = currentDeployment.provider_type === "kubernetes";

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
        return { key: "checking", variant: "outline" as const };
      }

      if (health.status === "healthy") {
        return { key: "healthy", variant: "default" as const };
      }
      if (health.status === "degraded") {
        return { key: "degraded", variant: "secondary" as const };
      }
      if (health.status === "down") {
        return { key: "down", variant: "destructive" as const };
      }
      if (health.status === "asleep") {
        return { key: "asleep", variant: "outline" as const };
      }
      if (health.status === "not_connected") {
        return { key: "not_connected", variant: "outline" as const };
      }
      return { key: "unknown", variant: "outline" as const };
    }

    // For failed deployments
    if (currentDeployment.status === "failed") {
      return { key: "failed", variant: "destructive" as const };
    }

    return null;
  };

  // Get progress badge info
  const getProgressBadge = () => {
    if (currentDeployment.status === "pending") {
      return { key: "queued", variant: "outline" as const };
    }
    if (
      ["initializing", "planning", "deploying"].includes(
        currentDeployment.status,
      )
    ) {
      return { key: "deploying", variant: "default" as const };
    }
    if (currentDeployment.status === "deleting") {
      return { key: "deleting", variant: "destructive" as const };
    }
    if (currentDeployment.status === "deleted") {
      return { key: "deleted", variant: "outline" as const };
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
              <AppIcon
                appId={currentDeployment.id}
                templateIcon={currentDeployment.template_icon}
                icon={currentDeployment.icon}
                className="h-5 w-5"
              />
              {currentDeployment.name}
            </CardTitle>
            <div className="flex items-center gap-2">
              {healthBadge && (
                <Badge
                  variant={healthBadge.variant}
                  className={`text-xs ${healthBadge.key === "degraded" ? "bg-orange-500 text-white hover:bg-orange-600" : ""}`}
                >
                  {t(`health.${healthBadge.key}`)}
                </Badge>
              )}
              {progressBadge && (
                <Badge variant={progressBadge.variant} className="text-xs">
                  {t(`progress.${progressBadge.key}`)}
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

          {/* Kubernetes Quick Actions */}
          {isKubernetes &&
            currentDeployment.status === "running" &&
            currentDeployment.github_repo_url && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  {t("card.quickActions")}
                </div>
                <div className="grid grid-cols-1 gap-2">
                  {appUrl && (
                    <a
                      href={appUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={buttonVariants({
                        variant: "outline",
                        size: "sm",
                        className: "justify-start",
                      })}
                    >
                      <Globe className="h-4 w-4 mr-2" />
                      {t("card.openApp")}
                      <ExternalLink className="h-3 w-3 ml-auto" />
                    </a>
                  )}
                  <a
                    href={currentDeployment.github_repo_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={buttonVariants({
                      variant: "outline",
                      size: "sm",
                      className: "justify-start",
                    })}
                  >
                    <Github className="h-4 w-4 mr-2" />
                    {t("card.openRepo")}
                    <ExternalLink className="h-3 w-3 ml-auto" />
                  </a>
                  {currentDeployment.argocd_app_name && (
                    <a
                      href={`https://argocd.3istor.com/applications/${currentDeployment.argocd_app_name}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={buttonVariants({
                        variant: "outline",
                        size: "sm",
                        className: "justify-start",
                      })}
                    >
                      <FileCode className="h-4 w-4 mr-2" />
                      {t("card.viewArgocd")}
                      <ExternalLink className="h-3 w-3 ml-auto" />
                    </a>
                  )}
                  {currentDeployment.project_id && (
                    <a
                      href={`https://vault.3istor.com/ui/vault/secrets/kvv2/show/projects/${currentDeployment.project_id}/${currentDeployment.name}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={buttonVariants({
                        variant: "outline",
                        size: "sm",
                        className: "justify-start",
                      })}
                    >
                      <Shield className="h-4 w-4 mr-2" />
                      {t("card.manageSecrets")}
                      <ExternalLink className="h-3 w-3 ml-auto" />
                    </a>
                  )}
                </div>
                {currentDeployment.k8s_namespace && (
                  <div className="rounded-md bg-muted p-2 text-xs">
                    <span className="text-muted-foreground">
                      {t("card.namespace")}{" "}
                    </span>
                    <span className="font-mono">
                      {currentDeployment.k8s_namespace}
                    </span>
                  </div>
                )}
              </div>
            )}

          {/* Legacy Outputs */}
          {!isKubernetes &&
            currentDeployment.status === "running" &&
            Object.keys(outputs).length > 0 && (
              <div className="rounded-md bg-muted p-3 space-y-1 text-xs font-mono">
                <div className="font-semibold text-muted-foreground mb-2">
                  {t("card.outputs")}
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
                {t("card.healthDetails")}
              </Button>
            )}
            {canDelete && (
              <Button
                variant="destructive"
                size="sm"
                onClick={handleDeleteClick}
                disabled={["deleting", "deleted", "pending"].includes(
                  currentDeployment.status,
                )}
              >
                {t("card.delete")}
              </Button>
            )}
          </div>
        </CardFooter>
      </Card>

      {/* Double-confirmation dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirmStep === 1
                ? t("card.confirmTitle")
                : (
                  <span className="inline-flex items-center gap-2">
                    <AlertTriangle className="h-5 w-5 text-destructive" />
                    {t("card.confirmSureTitle")}
                  </span>
                )}
            </DialogTitle>
            <DialogDescription>
              {confirmStep === 1
                ? t("card.confirmText", { name: currentDeployment.name })
                : currentDeployment.resource_count
                  ? t("card.confirmSureText", {
                      count: currentDeployment.resource_count,
                      name: currentDeployment.name,
                    })
                  : t("card.confirmSureUnknownText", {
                      name: currentDeployment.name,
                    })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t("card.cancel")}
            </Button>
            <Button variant="destructive" onClick={handleConfirm}>
              {confirmStep === 1
                ? t("card.yesDelete")
                : t("card.confirmDestroy")}
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
