"use client";

import { CostPerRequestCard } from "@/components/observability/CostPerRequestCard";
import { useObsCapabilities } from "@/components/observability/shared";
import { AppCostsPanel } from "@/components/finops/AppCostsPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppCostsPage() {
  const { projectName, appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  const member =
    deployment.provider_type === "kubernetes" && can("app.viewObservability");
  const caps = useObsCapabilities(appId, member);
  return (
    <AppSection allowed={can("finops.view")}>
      <div className="space-y-6">
        {member && caps.data?.metrics && <CostPerRequestCard appId={appId} />}
        <AppCostsPanel
          project={projectName}
          appId={appId}
          canAct={can("finops.act")}
          canSleep={deployment.provider_type === "kubernetes" && can("app.operate")}
        />
      </div>
    </AppSection>
  );
}
