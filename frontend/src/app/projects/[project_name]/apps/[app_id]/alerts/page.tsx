"use client";

import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppAlertsPage() {
  const { projectName, deployment } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection
      allowed={
        deployment.provider_type === "kubernetes" &&
        can("app.viewObservability")
      }
    >
      <AlertsPanel project={projectName} app={deployment.name} />
    </AppSection>
  );
}
