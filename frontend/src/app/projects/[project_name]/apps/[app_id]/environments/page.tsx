"use client";

import { EnvironmentsTab } from "@/components/environments/EnvironmentsTab";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppEnvironmentsPage() {
  const { appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection
      allowed={deployment.provider_type === "kubernetes" && can("release.view")}
    >
      <EnvironmentsTab deploymentId={appId} />
    </AppSection>
  );
}
