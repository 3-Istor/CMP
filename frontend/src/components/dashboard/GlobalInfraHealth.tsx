"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useGlobalHealth } from "@/lib/hooks";
import {
    AlertTriangle,
    CheckCircle2,
    Cloud,
    Server,
    XCircle,
} from "lucide-react";

const CLUSTER_BADGE = {
  up: { variant: "default", className: "" },
  down: { variant: "secondary", className: "text-muted-foreground" },
  starting: {
    variant: "secondary",
    className: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  },
  unknown: { variant: "outline", className: "text-muted-foreground" },
} as const;

export function GlobalInfraHealth() {
  const { health, loading, error } = useGlobalHealth(15000);

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="h-4 w-4" />
            Infrastructure Health
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-destructive">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4" />
            Infrastructure Health - Error
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{error}</p>
        </CardContent>
      </Card>
    );
  }

  if (!health) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Server className="h-4 w-4" />
          Infrastructure Health
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* OpenStack Hypervisors */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Server className="h-4 w-4 text-muted-foreground" />
              OpenStack Hypervisors
            </div>
            <div className="space-y-1.5">
              {health.openstack_hypervisors.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No hypervisors found
                </p>
              ) : (
                health.openstack_hypervisors.map((hv) => (
                  <div
                    key={hv.name}
                    className="flex items-center justify-between text-xs bg-muted/50 rounded px-2 py-1.5"
                  >
                    <div className="flex flex-col gap-0.5">
                      <span className="font-mono text-[10px]">{hv.name}</span>
                      {hv.ip && (
                        <span className="text-[10px] text-muted-foreground">
                          {hv.ip}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5">
                      {hv.state === "up" ? (
                        <CheckCircle2 className="h-3 w-3 text-green-500" />
                      ) : (
                        <XCircle className="h-3 w-3 text-red-500" />
                      )}
                      <Badge
                        variant={hv.state === "up" ? "default" : "destructive"}
                        className="text-[10px] h-4 px-1.5"
                      >
                        {hv.state}
                      </Badge>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {health.clusters
            .slice()
            .sort((x, y) => x.cloud.localeCompare(y.cloud))
            .map((c) => (
              <div key={c.cloud} className="space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Cloud className="h-4 w-4 text-muted-foreground" />
                  {c.cloud === "aws" ? "EKS" : "GKE"}
                </div>
                <div className="flex items-center justify-between text-xs bg-muted/50 rounded px-2 py-1.5">
                  <span className="font-mono text-[10px]">
                    {c.cloud === "aws" ? "EKS" : "GKE"}
                  </span>
                  <Badge
                    variant={CLUSTER_BADGE[c.state].variant}
                    className={`text-[10px] h-4 px-1.5 ${CLUSTER_BADGE[c.state].className}`}
                  >
                    {c.state}
                  </Badge>
                </div>
              </div>
            ))}
        </div>
      </CardContent>
    </Card>
  );
}
