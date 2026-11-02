"use client";

import { useAppContext } from "@/components/projects/AppContext";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { DeploymentHealth } from "@/components/projects/DeploymentHealth";
import { DeploymentLogs } from "@/components/projects/DeploymentLogs";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

export default function AppOverviewPage() {
  const { appId, deployment: current } = useAppContext();
  const { can } = useProjectContext();
  // null = follow the status (collapsed once running) until the user toggles.
  const [statusOpen, setStatusOpen] = useState<boolean | null>(null);

  const canViewLogs = can("app.viewLogs");
  const canViewConfig = can("app.viewConfig");
  const isKubernetes = current.provider_type === "kubernetes";
  const isRunning = current.status === "running";
  const shouldFetchHealth =
    current.status === "running" || current.status === "degraded";

  return (
    <>
      {/* Deployment progress, collapsed by default once running */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Deployment Status</CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setStatusOpen(!(statusOpen ?? !isRunning))}
            className="h-8 w-8"
            title={statusOpen ?? !isRunning ? "Collapse" : "Expand"}
          >
            {statusOpen ?? !isRunning ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </Button>
        </CardHeader>
        {(statusOpen ?? !isRunning) && (
          <CardContent>
            <DeploymentStepper deployment={current} />
          </CardContent>
        )}
      </Card>

      {/* Live deployment log streaming, members and up.
          A guest sees the status badge and the stepper, not
          the build output. */}
      {canViewLogs && (
        <DeploymentLogs
          deploymentId={appId}
          deploymentStatus={current.status}
        />
      )}

      {/* Health summary (inline, collapsible like the logs) */}
      {shouldFetchHealth && canViewLogs && (
        <DeploymentHealth deploymentId={appId} />
      )}

      {/* Legacy deployments: show Terraform outputs */}
      {!isKubernetes && canViewConfig && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Terraform Outputs</CardTitle>
          </CardHeader>
          <CardContent>
            {current.terraform_outputs ? (
              <div className="space-y-1.5 text-xs font-mono">
                {Object.entries(
                  (() => {
                    try {
                      return JSON.parse(current.terraform_outputs) as Record<
                        string,
                        unknown
                      >;
                    } catch {
                      return {};
                    }
                  })(),
                ).map(([key, value]) => (
                  <div key={key} className="flex gap-2 flex-wrap">
                    <span className="text-muted-foreground shrink-0">
                      {key}:
                    </span>
                    {typeof value === "string" && value.startsWith("http") ? (
                      <a
                        href={value}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline break-all"
                      >
                        {value}
                      </a>
                    ) : (
                      <span className="break-all">{String(value)}</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No outputs yet.</p>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
