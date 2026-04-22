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
import {
    AlertTriangle,
    CheckCircle2,
    Cloud,
    Database,
    RefreshCw,
    XCircle,
} from "lucide-react";

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
  const { health, loading, error, refresh } = useAppHealth(
    open ? deploymentId : null,
    5000,
  );

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "healthy":
        return <CheckCircle2 className="h-5 w-5 text-green-500" />;
      case "degraded":
        return <AlertTriangle className="h-5 w-5 text-yellow-500" />;
      case "down":
        return <XCircle className="h-5 w-5 text-red-500" />;
      default:
        return <AlertTriangle className="h-5 w-5 text-gray-400" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "healthy":
        return "bg-green-500";
      case "degraded":
        return "bg-yellow-500";
      case "down":
        return "bg-red-500";
      default:
        return "bg-gray-400";
    }
  };

  const getHealthBadgeVariant = (
    health: string | null,
  ): "default" | "secondary" | "destructive" => {
    if (health === "healthy") return "default";
    if (health === "unhealthy") return "destructive";
    return "secondary";
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Health Details: {deploymentName}
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
            Real-time health status from cloud APIs
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
                Failed to fetch health data
              </p>
              <p className="text-sm text-muted-foreground">{error}</p>
            </div>
          </div>
        ) : health ? (
          <div className="space-y-6">
            {/* Overall Status */}
            <div className="flex items-center gap-3 p-4 bg-muted/50 rounded-lg">
              {getStatusIcon(health.status)}
              <div className="flex-1">
                <p className="font-medium">Overall Status</p>
                <p className="text-sm text-muted-foreground">
                  {health.status === "healthy"
                    ? "All components are operational"
                    : health.status === "degraded"
                      ? "Some components are unhealthy or provisioning"
                      : health.status === "down"
                        ? "No healthy components detected"
                        : "Unable to determine status"}
                </p>
              </div>
              <Badge
                variant={
                  health.status === "healthy"
                    ? "default"
                    : health.status === "degraded"
                      ? "secondary"
                      : "destructive"
                }
                className="text-sm capitalize"
              >
                {health.status}
              </Badge>
            </div>

            {/* AWS Frontend */}
            {health.aws_frontend && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Cloud className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold text-sm">AWS Frontend Layer</h3>
                </div>

                <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">ASG Name:</span>
                    <span className="font-mono">
                      {health.aws_frontend.asg_name}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">
                      Desired Capacity:
                    </span>
                    <span className="font-mono">
                      {health.aws_frontend.desired_capacity}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Health:</span>
                    <span className="font-semibold">
                      {health.aws_frontend.healthy_count} /{" "}
                      {health.aws_frontend.total_count} healthy
                    </span>
                  </div>
                </div>

                {health.aws_frontend.instances.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-muted-foreground">
                      Instances:
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
                    No instances found
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
                    OpenStack Backend Layer
                  </h3>
                </div>

                <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Health:</span>
                    <span className="font-semibold">
                      {health.openstack_backend.healthy_count} /{" "}
                      {health.openstack_backend.total_count} healthy
                    </span>
                  </div>
                </div>

                {health.openstack_backend.servers.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-muted-foreground">
                      Database Servers:
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
                    No servers found
                  </p>
                )}
              </div>
            )}

            {!health.aws_frontend && !health.openstack_backend && (
              <div className="text-center py-8 text-muted-foreground">
                <p>No infrastructure components found</p>
                <p className="text-xs mt-1">
                  This deployment may not have been created yet or uses a
                  different architecture
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
  const isHealthy = instance.health === "healthy";
  const isUnhealthy = instance.health === "unhealthy";
  const isActive = instance.state === "active" || instance.state === "running";

  return (
    <div className="flex items-center justify-between bg-muted/50 rounded-lg p-3 text-xs">
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
        <div className="flex flex-col gap-1 min-w-0">
          <span className="font-mono text-[10px] truncate">
            {instance.instance_id}
          </span>
          {instance.private_ip && (
            <span className="text-muted-foreground font-mono text-[10px]">
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
            {instance.health}
          </Badge>
        )}
      </div>
    </div>
  );
}
