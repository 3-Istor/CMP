"use client";

import { LogsView } from "@/components/app-overview/LogsView";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppLogsPage() {
  const { appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection
      allowed={deployment.provider_type === "kubernetes" && can("app.viewLogs")}
    >
      <LogsView appId={appId} />
    </AppSection>
  );
}
