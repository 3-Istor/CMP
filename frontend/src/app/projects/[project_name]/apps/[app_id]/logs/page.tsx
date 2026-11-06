"use client";

import { LogsView } from "@/components/activity/LogsView";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppLogsPage() {
  const { deployment } = useAppContext();
  const { can, projectName } = useProjectContext();
  const namespace =
    deployment.k8s_namespace ?? `${projectName}-${deployment.name}`;
  return (
    <AppSection
      allowed={deployment.provider_type === "kubernetes" && can("app.viewLogs")}
    >
      <LogsView project={projectName} namespace={namespace} />
    </AppSection>
  );
}
