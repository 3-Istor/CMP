"use client";

import { ComponentsCard } from "@/components/app-overview/ComponentsCard";
import { DeploymentsCard } from "@/components/app-overview/DeploymentsCard";
import { EventsCard } from "@/components/app-overview/EventsCard";
import { LogsCard } from "@/components/app-overview/LogsCard";
import {
  CostCard,
  MetricCards,
} from "@/components/app-overview/MetricCards";
import { useAppContext } from "@/components/projects/AppContext";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getDeploymentLinks } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { useTranslations } from "next-intl";

export default function AppOverviewPage() {
  const { appId, projectName, deployment: current } = useAppContext();
  const { can } = useProjectContext();

  const isKubernetes = current.provider_type === "kubernetes";
  const canViewLogs = can("app.viewLogs");
  const canViewConfig = can("app.viewConfig");
  const base = `/projects/${encodeURIComponent(projectName)}/apps/${appId}`;

  const links = useCardData(
    `links-${appId}-${current.github_repo_url ?? ""}`,
    () => getDeploymentLinks(appId),
    { enabled: isKubernetes && canViewLogs },
  );

  if (!isKubernetes) {
    return canViewConfig ? <TerraformOutputs outputs={current.terraform_outputs} /> : null;
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCards
          appId={appId}
          links={links.data ?? []}
          isKubernetes={isKubernetes}
          allowed={canViewLogs}
        />
        <CostCard
          appId={appId}
          projectName={projectName}
          allowed={can("finops.view")}
        />
      </div>

      <ComponentsCard appId={appId} links={links.data ?? []} />

      <LogsCard appId={appId} base={base} allowed={canViewLogs} />

      <DeploymentsCard appId={appId} base={base} canOpen={canViewLogs} />

      <EventsCard appId={appId} allowed={canViewLogs} />

    </>
  );
}

function TerraformOutputs({ outputs }: { outputs: string | null }) {
  const parsed = (() => {
    try {
      return outputs ? (JSON.parse(outputs) as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  })();
  const t = useTranslations("appPage.legacy");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("outputs")}</CardTitle>
      </CardHeader>
      <CardContent>
        {parsed ? (
          <div className="space-y-1.5 font-mono text-xs">
            {Object.entries(parsed).map(([key, value]) => (
              <div key={key} className="flex flex-wrap gap-2">
                <span className="shrink-0 text-muted-foreground">{key}:</span>
                {typeof value === "string" && value.startsWith("http") ? (
                  <a
                    href={value}
                    target="_blank"
                    rel="noreferrer"
                    className="break-all text-primary underline"
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
          <p className="text-sm text-muted-foreground">{t("noOutputs")}</p>
        )}
      </CardContent>
    </Card>
  );
}
