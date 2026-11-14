"use client";

import { CostPerRequestCard } from "@/components/observability/CostPerRequestCard";
import { useObsCapabilities } from "@/components/observability/shared";
import { ProjectFinopsPanel } from "@/components/finops/ProjectFinopsPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function AppCostsPage() {
  const t = useTranslations("apps.costs");
  const { projectName, appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  const member =
    deployment.provider_type === "kubernetes" && can("app.viewObservability");
  const caps = useObsCapabilities(appId, member);
  return (
    <AppSection allowed={can("finops.view")}>
      <div className="space-y-3">
        {member && caps.data?.metrics && <CostPerRequestCard appId={appId} />}
        <p className="text-sm text-muted-foreground">
          {t("projectLevel")}
        </p>
        <ProjectFinopsPanel project={projectName} />
      </div>
    </AppSection>
  );
}
