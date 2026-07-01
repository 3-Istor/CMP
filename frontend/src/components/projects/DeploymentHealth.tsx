"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAppHealth } from "@/lib/hooks";
import type { VMInstance } from "@/types";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cloud,
  Database,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useState } from "react";

interface Props {
  deploymentId: number;
}

function statusIcon(status: string) {
  switch (status) {
    case "healthy":
      return <CheckCircle2 className="h-5 w-5 text-green-500" />;
    case "degraded":
      return <AlertTriangle className="h-5 w-5 text-orange-500" />;
    case "down":
      return <XCircle className="h-5 w-5 text-red-500" />;
    default:
      return <AlertTriangle className="h-5 w-5 text-gray-400" />;
  }
}

/**
 * Inline, collapsible health panel (same data as the old dialog, but expandable
 * in-page like the logs console). Collapsed by default; the header always shows
 * the overall status so it's readable at a glance.
 */
export function DeploymentHealth({ deploymentId }: Props) {
  const [isExpanded, setIsExpanded] = useState(false);
  const { health, loading, error, refresh } = useAppHealth(deploymentId, 10_000);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {health ? statusIcon(health.status) : (
            <AlertTriangle className="h-5 w-5 text-gray-400" />
          )}
          Health Summary
          {health && (
            <Badge
              variant={
                health.status === "healthy"
                  ? "default"
                  : health.status === "degraded"
                    ? "secondary"
                    : "destructive"
              }
              className="capitalize text-xs"
            >
              {health.status}
            </Badge>
          )}
        </CardTitle>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={refresh}
            disabled={loading}
            className="h-8 w-8"
            title="Refresh health"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsExpanded((v) => !v)}
            className="h-8 w-8"
            title={isExpanded ? "Collapse" : "Expand"}
          >
            {isExpanded ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </Button>
        </div>
      </CardHeader>

      {isExpanded && (
        <CardContent>
          {loading && !health ? (
            <div className="space-y-4">
              <Skeleton className="h-20" />
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
            <div className="space-y-4">
              {/* Overall status */}
              <div className="flex items-center justify-between gap-3 p-3 bg-muted/50 rounded-lg">
                <div className="flex items-center gap-3">
                  {statusIcon(health.status)}
                  <div>
                    <p className="font-medium text-sm">Overall Status</p>
                    <p className="text-xs text-muted-foreground">
                      {health.status === "healthy"
                        ? "All components operational"
                        : health.status === "degraded"
                          ? "Some components unhealthy"
                          : health.status === "down"
                            ? "No healthy components"
                            : "Unable to determine"}
                    </p>
                  </div>
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
                      <span className="font-mono text-xs">
                        {health.aws_frontend.asg_name}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        Desired Capacity:
                      </span>
                      <span className="font-mono text-xs">
                        {health.aws_frontend.desired_capacity}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">Health:</span>
                      <span className="font-semibold text-xs">
                        {health.aws_frontend.healthy_count} /{" "}
                        {health.aws_frontend.total_count} healthy
                      </span>
                    </div>
                  </div>

                  {health.aws_frontend.instances.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        Instances ({health.aws_frontend.instances.length}):
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
                    <h3 className="font-semibold text-sm">OpenStack Layer</h3>
                  </div>

                  <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">Health:</span>
                      <span className="font-semibold text-xs">
                        {health.openstack_backend.healthy_count} /{" "}
                        {health.openstack_backend.total_count} healthy
                      </span>
                    </div>
                  </div>

                  {health.openstack_backend.servers.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        Servers ({health.openstack_backend.servers.length}):
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
                <div className="text-center py-6 text-muted-foreground">
                  <p>No infrastructure components found</p>
                </div>
              )}
            </div>
          ) : null}
        </CardContent>
      )}
    </Card>
  );
}

function InstanceCard({ instance }: { instance: VMInstance }) {
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
            {instance.health}
          </Badge>
        )}
      </div>
    </div>
  );
}
