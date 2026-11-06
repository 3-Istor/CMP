"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAppHealth } from "@/lib/hooks";
import type { VMInstance } from "@/types";
import { AlertTriangle, CheckCircle2, Cloud, Database, Moon, PlugZap, RefreshCw, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";

interface Props {
  deploymentId: number;
  deploymentName: string;
  open: boolean;
  onClose: () => void;
}

export function DeploymentHealthPanel({
  deploymentId,
  deploymentName,
  open,
  onClose,
}: Props) {
  const t = useTranslations("dashboard");
  const { health, loading, error, refresh } = useAppHealth(
    open ? deploymentId : null,
    5000,
  );

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "healthy":
        return <CheckCircle2 className="h-5 w-5 text-green-700 dark:text-green-500" />;
      case "degraded":
        return <AlertTriangle className="h-5 w-5 text-yellow-700 dark:text-yellow-500" />;
      case "down":
        return <XCircle className="h-5 w-5 text-red-700 dark:text-red-500" />;
      case "asleep":
        return <Moon className="h-5 w-5 text-indigo-400" />;
      case "not_connected":
        return <PlugZap className="h-5 w-5 text-muted-foreground" />;
      default:
        return <AlertTriangle className="h-5 w-5 text-muted-foreground" />;
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-7xl sm:max-w-7xl md:max-w-7xl lg:max-w-7xl w-[95vw] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {t("healthPanel.title", { name: deploymentName })}
            <Button
              variant="ghost"
              size="sm"
              onClick={refresh}
              disabled={loading}
              className="ml-auto"
            >
              <RefreshCw
                className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
              />
            </Button>
          </DialogTitle>
          <DialogDescription>
            {t("healthPanel.description")}
          </DialogDescription>
        </DialogHeader>

        {loading && !health ? (
          <div className="space-y-4">
            <Skeleton className="h-20" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </div>
        ) : error ? (
          <div className="flex items-center gap-3 p-4 bg-destructive/10 rounded-lg border border-destructive">
            <XCircle className="h-5 w-5 text-destructive" />
            <div>
              <p className="font-medium text-destructive">
                {t("healthPanel.fetchFailed")}
              </p>
              <p className="text-sm text-muted-foreground">{error}</p>
            </div>
          </div>
        ) : health ? (
          <div className="space-y-4">
            {/* Overall Status - Compact */}
            <div className="flex items-center justify-between gap-3 p-3 bg-muted/50 rounded-lg">
              <div className="flex items-center gap-3">
                {getStatusIcon(health.status)}
                <div>
                  <p className="font-medium text-sm">{t("healthPanel.overall")}</p>
                  <p className="text-xs text-muted-foreground">
                    {health.status === "healthy"
                      ? t("healthPanel.overallHealthy")
                      : health.status === "degraded"
                        ? t("healthPanel.overallDegraded")
                        : health.status === "down"
                          ? t("healthPanel.overallDown")
                          : t("healthPanel.overallUnknown")}
                  </p>
                </div>
              </div>
              <Badge
                variant={
                  health.status === "healthy"
                    ? "default"
                    : health.status === "degraded"
                      ? "secondary"
                      : health.status === "down"
                        ? "destructive"
                        : "outline"
                }
                className="text-sm capitalize"
              >
                {t(`health.${health.status}`)}
              </Badge>
            </div>

            {/* Two-column layout for AWS and OpenStack */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* AWS Frontend */}
              {health.aws_frontend && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Cloud className="h-4 w-4 text-muted-foreground" />
                    <h3 className="font-semibold text-sm">
                      {t("healthPanel.awsLayer")}
                    </h3>
                  </div>

                  <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("healthPanel.asgName")}:
                      </span>
                      <span className="font-mono text-xs">
                        {health.aws_frontend.asg_name}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("healthPanel.desiredCapacity")}:
                      </span>
                      <span className="font-mono text-xs">
                        {health.aws_frontend.desired_capacity}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("healthPanel.health")}:
                      </span>
                      <span className="font-semibold text-xs">
                        {t("healthPanel.counts", {
                          healthy: health.aws_frontend.healthy_count,
                          total: health.aws_frontend.total_count,
                        })}
                      </span>
                    </div>
                  </div>

                  {health.aws_frontend.instances.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        {t("healthPanel.instances", {
                          count: health.aws_frontend.instances.length,
                        })}
                      </p>
                      {health.aws_frontend.instances.map((instance) => (
                        <InstanceCard
                          key={instance.instance_id}
                          instance={instance}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {t("healthPanel.noInstances")}
                    </p>
                  )}
                </div>
              )}

              {/* OpenStack Backend */}
              {health.openstack_backend && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4 text-muted-foreground" />
                    <h3 className="font-semibold text-sm">
                      {t("healthPanel.openstackLayer")}
                    </h3>
                  </div>

                  <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("healthPanel.health")}:
                      </span>
                      <span className="font-semibold text-xs">
                        {t("healthPanel.counts", {
                          healthy: health.openstack_backend.healthy_count,
                          total: health.openstack_backend.total_count,
                        })}
                      </span>
                    </div>
                  </div>

                  {health.openstack_backend.servers.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        {t("healthPanel.servers", {
                          count: health.openstack_backend.servers.length,
                        })}
                      </p>
                      {health.openstack_backend.servers.map((server) => (
                        <InstanceCard
                          key={server.instance_id}
                          instance={server}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {t("healthPanel.noServers")}
                    </p>
                  )}
                </div>
              )}
            </div>

            {!health.aws_frontend && !health.openstack_backend && (
              <div className="text-center py-8 text-muted-foreground">
                <p>{t("healthPanel.noComponents")}</p>
                <p className="text-xs mt-1">
                  {t("healthPanel.noComponentsHelp")}
                </p>
              </div>
            )}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function InstanceCard({ instance }: { instance: VMInstance }) {
  const t = useTranslations("dashboard");
  const isHealthy = instance.health === "healthy";
  const isUnhealthy = instance.health === "unhealthy";
  const isActive = instance.state === "active" || instance.state === "running";

  return (
    <div className="flex items-center justify-between bg-muted/50 rounded-lg p-3 text-xs gap-4">
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <div
          className={`h-2 w-2 rounded-full shrink-0 ${
            isHealthy
              ? "bg-green-500"
              : isUnhealthy
                ? "bg-red-500"
                : "bg-yellow-500"
          }`}
        />
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <span className="font-mono text-xs truncate min-w-[200px]">
            {instance.instance_id}
          </span>
          {instance.private_ip && (
            <span className="text-muted-foreground font-mono text-xs">
              {instance.private_ip}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Badge
          variant={isActive ? "default" : "secondary"}
          className="text-[10px] h-5 px-2"
        >
          {instance.state}
        </Badge>
        {instance.health && (
          <Badge
            variant={
              isHealthy ? "default" : isUnhealthy ? "destructive" : "secondary"
            }
            className="text-[10px] h-5 px-2"
          >
            {isHealthy || isUnhealthy
              ? t(`instanceHealth.${instance.health}`)
              : instance.health}
          </Badge>
        )}
      </div>
    </div>
  );
}
