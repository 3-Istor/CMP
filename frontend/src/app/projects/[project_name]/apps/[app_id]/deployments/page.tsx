"use client";

import { DeploymentsTable } from "@/components/app-overview/DeploymentsCard";
import { OverviewCard } from "@/components/app-overview/OverviewCard";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { DeploymentLogs } from "@/components/projects/DeploymentLogs";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { Card, CardContent } from "@/components/ui/card";
import { getReleaseHistory } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { useTranslations } from "next-intl";

const SHOWN_WHILE = ["pending", "initializing", "planning", "deploying", "deleting", "failed"];

export default function AppDeploymentsPage() {
  const { appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  const t = useTranslations("appPage.deployments");
  const isKubernetes = deployment.provider_type === "kubernetes";
  const history = useCardData(
    `deployments-full-${appId}`,
    () => getReleaseHistory(appId),
    { intervalMs: 15_000, enabled: isKubernetes },
  );

  return (
    <AppSection allowed={can("app.viewLogs")}>
      {SHOWN_WHILE.includes(deployment.status) && (
        <Card>
          <CardContent>
            <DeploymentStepper deployment={deployment} />
          </CardContent>
        </Card>
      )}

      {isKubernetes && (
        <OverviewCard
          title={t("history")}
          state={{
            loading: history.loading,
            error: history.error,
            forbidden: history.forbidden,
            stale: history.stale,
            updatedAt: history.updatedAt,
            hasData: history.data !== null,
            empty: history.data !== null && history.data.length === 0,
            retry: history.retry,
          }}
          emptyText={t("empty")}
          skeletonClassName="h-64 w-full"
        >
          <DeploymentsTable events={history.data ?? []} />
        </OverviewCard>
      )}

      <DeploymentLogs deploymentId={appId} deploymentStatus={deployment.status} />
    </AppSection>
  );
}
