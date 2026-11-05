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
import { Input } from "@/components/ui/input";
import { getReleaseHistory, startPromotion } from "@/lib/api";
import { useAppHealth } from "@/lib/hooks";
import { useCardData } from "@/lib/useCardData";
import { useDebounced } from "@/lib/useDebounced";
import type { ReleaseEvent } from "@/types";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

const SHOWN_WHILE = ["pending", "initializing", "planning", "deploying", "deleting", "failed"];
const ENVIRONMENTS = ["staging", "prod"] as const;
const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

export default function AppDeploymentsPage() {
  const { appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  const t = useTranslations("appPage.deployments");
  const th = useTranslations("health.components");
  const isKubernetes = deployment.provider_type === "kubernetes";
  const [rollingBack, setRollingBack] = useState<ReleaseEvent | null>(null);
  const [environment, setEnvironment] = useState("");
  const [component, setComponent] = useState("");
  const [search, setSearch] = useState("");
  const version = useDebounced(search.trim(), 400);
  const filtered = Boolean(environment || component || version);
  const { health } = useAppHealth(appId, 0);
  const components = (health?.components ?? []).map((c) => c.name);
  const history = useCardData(
    `deployments-full-${appId}|${environment}|${component}|${version}`,
    () => getReleaseHistory(appId, { environment, component, version }),
    { intervalMs: 15_000, enabled: isKubernetes },
  );

  const rollbackVersion = imageTag(rollingBack?.image);
  const rollBack = async () => {
    // rollingBack is already null while the dialog closes; without an id this would be a normal promotion.
    const id = rollingBack?.rollback_promotion_id;
    if (id == null) return;
    // Same call as the promotion history; the backend refuses if a promotion runs or prod is already there.
    await startPromotion(appId, { rollbackTo: id });
    toast.success(t("rollbackStarted", { version: rollbackVersion }));
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
          action={
            <div className="flex flex-wrap items-center justify-end gap-2">
              <select
                aria-label={t("env")}
                className={SELECT_CLASS}
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
              >
                <option value="">{t("allEnvironments")}</option>
                {ENVIRONMENTS.map((env) => (
                  <option key={env} value={env}>
                    {env}
                  </option>
                ))}
              </select>
              {components.length > 1 && (
                <select
                  aria-label={t("component")}
                  className={SELECT_CLASS}
                  value={component}
                  onChange={(e) => setComponent(e.target.value)}
                >
                  <option value="">{t("allComponents")}</option>
                  {components.map((c) => (
                    <option key={c} value={c}>
                      {th.has(c) ? th(c) : c}
                    </option>
                  ))}
                </select>
              )}
              <Input
                type="search"
                aria-label={t("version")}
                maxLength={128}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("searchPlaceholder")}
                className="h-8 w-40"
              />
            </div>
          }
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
          emptyText={filtered ? t("noMatch") : t("empty")}
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
        title={t("rollbackTitle", { version: rollbackVersion })}
        description={t("rollbackDescription")}
        confirmLabel={t("rollback")}
        onConfirm={rollBack}
      />

      <DeploymentLogs deploymentId={appId} deploymentStatus={deployment.status} />
    </AppSection>
  );
}
