"use client";

import { DeploymentsTable } from "@/components/app-overview/DeploymentsCard";
import { OverviewCard } from "@/components/app-overview/OverviewCard";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { DeploymentLogs } from "@/components/projects/DeploymentLogs";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { imageTag } from "@/components/release/format";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { getReleaseHistory, startPromotion } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import type { ReleaseEvent } from "@/types";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

const SHOWN_WHILE = ["pending", "initializing", "planning", "deploying", "deleting", "failed"];

export default function AppDeploymentsPage() {
  const { appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  const t = useTranslations("appPage.deployments");
  const isKubernetes = deployment.provider_type === "kubernetes";
  const [rollingBack, setRollingBack] = useState<ReleaseEvent | null>(null);
  const history = useCardData(
    `deployments-full-${appId}`,
    () => getReleaseHistory(appId),
    { intervalMs: 15_000, enabled: isKubernetes },
  );

  const version = imageTag(rollingBack?.image);
  const rollBack = async () => {
    // rollingBack is already null while the dialog closes; without an id this would be a normal promotion.
    const id = rollingBack?.rollback_promotion_id;
    if (id == null) return;
    // Same call as the promotion history; the backend refuses if a promotion runs or prod is already there.
    await startPromotion(appId, { rollbackTo: id });
    toast.success(t("rollbackStarted", { version }));
    history.retry();
  };

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
          <DeploymentsTable
            events={history.data ?? []}
            onRollback={can("release.operate") ? setRollingBack : undefined}
          />
        </OverviewCard>
      )}

      <ConfirmDialog
        open={rollingBack !== null}
        onOpenChange={(open) => !open && setRollingBack(null)}
        level={1}
        title={t("rollbackTitle", { version })}
        description={t("rollbackDescription")}
        confirmLabel={t("rollback")}
        onConfirm={rollBack}
      />

      <DeploymentLogs deploymentId={appId} deploymentStatus={deployment.status} />
    </AppSection>
  );
}
