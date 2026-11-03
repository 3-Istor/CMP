"use client";

import { ActivityTab } from "@/components/activity/ActivityTab";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppActivityPage() {
  const { projectName, deployment } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection
      allowed={
        deployment.provider_type === "kubernetes" &&
        can("app.viewObservability")
      }
    >
      <ActivityTab project={projectName} app={deployment.name} />
    </AppSection>
  );
}
