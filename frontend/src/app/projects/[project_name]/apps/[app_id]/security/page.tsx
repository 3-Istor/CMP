"use client";

import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { SecurityDashboard } from "@/components/security/SecurityDashboard";

export default function AppSecurityPage() {
  const { projectName, deployment } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection
      allowed={
        deployment.provider_type === "kubernetes" &&
        can("app.viewObservability")
      }
    >
      <SecurityDashboard
        project={projectName}
        app={deployment.name}
        fullPageLink
      />
    </AppSection>
  );
}
