"use client";

import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { ReleaseTab } from "@/components/release/ReleaseTab";

export default function AppReleasePage() {
  const { appId, projectName, deployment } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection
      allowed={deployment.provider_type === "kubernetes" && can("release.view")}
    >
      <ReleaseTab deploymentId={appId} projectName={projectName} />
    </AppSection>
  );
}
