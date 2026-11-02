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
import type { AppHealthStatus, ComponentHealth, VMInstance } from "@/types";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cloud,
  CloudOff,
  Database,
  HelpCircle,
  Moon,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

interface Props {
  deploymentId: number;
}

const STATE_STYLE: Record<
  AppHealthStatus,
  { Icon: typeof CheckCircle2; color: string }
> = {
  healthy: { Icon: CheckCircle2, color: "text-green-600 dark:text-green-400" },
  degraded: { Icon: AlertTriangle, color: "text-orange-600 dark:text-orange-400" },
  down: { Icon: XCircle, color: "text-red-600 dark:text-red-400" },
  asleep: { Icon: Moon, color: "text-blue-600 dark:text-blue-400" },
  not_connected: { Icon: CloudOff, color: "text-slate-600 dark:text-slate-400" },
  unknown: { Icon: HelpCircle, color: "text-gray-500 dark:text-gray-400" },
};

function stateStyle(state: string) {
  return STATE_STYLE[state as AppHealthStatus] ?? STATE_STYLE.unknown;
}

function StateIcon({ state }: { state: string }) {
  const { Icon, color } = stateStyle(state);
  return <Icon className={`h-5 w-5 ${color}`} aria-hidden="true" />;
}

function StateLabel({ state }: { state: string }) {
  const t = useTranslations("health.states");
  const { Icon, color } = stateStyle(state);
  return (
    <span className={`inline-flex items-center gap-1 font-medium ${color}`}>
      <Icon className="h-4 w-4" aria-hidden="true" />
      {t.has(state) ? t(state) : t("unknown")}
    </span>
  );
}

function ComponentRow({ component }: { component: ComponentHealth }) {
  const t = useTranslations("health");
  const argocd = [component.argocd_sync, component.argocd_health].filter(Boolean);
  return (
    <div className="bg-muted/50 rounded-lg p-3 text-xs space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold text-sm">
          {t.has(`components.${component.name}`)
            ? t(`components.${component.name}`)
            : component.name}
        </span>
        <StateLabel state={component.state} />
      </div>
      {component.reason && (
        <p className="text-muted-foreground">
          {t.has(`reasons.${component.reason}`)
            ? t(`reasons.${component.reason}`)
            : component.reason}
        </p>
      )}
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <div>
          <dt className="text-muted-foreground">{t("replicas")}</dt>
          <dd className="font-mono">
            {component.ready} / {component.desired}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("restarts")}</dt>
          <dd className="font-mono">{component.restarts_15m ?? "-"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("argocd")}</dt>
          <dd className="font-mono">
            {argocd.length > 0 ? argocd.join(" / ") : t("argocdUnknown")}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function badgeVariant(state: string) {
  if (state === "healthy") return "default" as const;
  if (state === "degraded" || state === "asleep" || state === "not_connected")
    return "secondary" as const;
  return "destructive" as const;
}

/**
 * Inline, collapsible health panel. Collapsed by default; the header always
 * shows the overall state. Kubernetes apps list one row per component, VM apps
 * keep their AWS and OpenStack layers.
 */
export function DeploymentHealth({ deploymentId }: Props) {
  const t = useTranslations("health");
  const [isExpanded, setIsExpanded] = useState(false);
  const { health, loading, error, refresh } = useAppHealth(deploymentId, 10_000);
  const components = health?.components ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {health ? (
            <StateIcon state={health.status} />
          ) : (
            <HelpCircle className="h-5 w-5 text-gray-500" aria-hidden="true" />
          )}
          {t("title")}
          {health && (
            <Badge variant={badgeVariant(health.status)} className="text-xs">
              <StateLabel state={health.status} />
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
            title={t("refresh")}
            aria-label={t("refresh")}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsExpanded((v) => !v)}
            className="h-8 w-8"
            title={isExpanded ? t("collapse") : t("expand")}
            aria-label={isExpanded ? t("collapse") : t("expand")}
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
              <XCircle className="h-5 w-5 text-destructive" aria-hidden="true" />
              <div>
                <p className="font-medium text-destructive">{t("fetchFailed")}</p>
                <p className="text-sm text-muted-foreground">{error}</p>
              </div>
            </div>
          ) : health ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3 p-3 bg-muted/50 rounded-lg">
                <div className="flex items-center gap-3">
                  <StateIcon state={health.status} />
                  <div>
                    <p className="font-medium text-sm">{t("overall")}</p>
                    <p className="text-xs text-muted-foreground">
                      {t.has(`summary.${health.status}`)
                        ? t(`summary.${health.status}`)
                        : t("summary.unknown")}
                    </p>
                  </div>
                </div>
                <Badge variant={badgeVariant(health.status)} className="text-sm">
                  <StateLabel state={health.status} />
                </Badge>
              </div>

              {components.length > 0 && (
                <div className="space-y-2">
                  {components.map((component) => (
                    <ComponentRow key={component.name} component={component} />
                  ))}
                </div>
              )}

              {health.aws_frontend && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Cloud className="h-4 w-4 text-muted-foreground" />
                    <h3 className="font-semibold text-sm">{t("legacy.aws")}</h3>
                  </div>

                  <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("legacy.asgName")}
                      </span>
                      <span className="font-mono text-xs">
                        {health.aws_frontend.asg_name}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("legacy.desired")}
                      </span>
                      <span className="font-mono text-xs">
                        {health.aws_frontend.desired_capacity}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("legacy.health")}
                      </span>
                      <span className="font-semibold text-xs">
                        {t("legacy.healthyOf", {
                          healthy: health.aws_frontend.healthy_count,
                          total: health.aws_frontend.total_count,
                        })}
                      </span>
                    </div>
                  </div>

                  {health.aws_frontend.instances.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        {t("legacy.instances", {
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
                      {t("legacy.noInstances")}
                    </p>
                  )}
                </div>
              )}

              {health.openstack_backend && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Database className="h-4 w-4 text-muted-foreground" />
                    <h3 className="font-semibold text-sm">
                      {t("legacy.openstack")}
                    </h3>
                  </div>

                  <div className="bg-muted/30 rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">
                        {t("legacy.health")}
                      </span>
                      <span className="font-semibold text-xs">
                        {t("legacy.healthyOf", {
                          healthy: health.openstack_backend.healthy_count,
                          total: health.openstack_backend.total_count,
                        })}
                      </span>
                    </div>
                  </div>

                  {health.openstack_backend.servers.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        {t("legacy.servers", {
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
                      {t("legacy.noServers")}
                    </p>
                  )}
                </div>
              )}

              {components.length === 0 &&
                !health.aws_frontend &&
                !health.openstack_backend && (
                  <div className="text-center py-6 text-muted-foreground">
                    <p>{t("noComponents")}</p>
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
