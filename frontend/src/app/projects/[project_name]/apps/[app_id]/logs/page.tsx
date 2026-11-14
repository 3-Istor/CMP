"use client";

import { LogsView } from "@/components/activity/LogsView";
import { LogsExplorer } from "@/components/observability/LogsExplorer";
import { useObsCapabilities } from "@/components/observability/shared";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Skeleton } from "@/components/ui/skeleton";
import { Suspense } from "react";

export default function AppLogsPage() {
  const { deployment, appId } = useAppContext();
  const { can, projectName } = useProjectContext();
  const allowed = deployment.provider_type === "kubernetes" && can("app.viewLogs");
  const caps = useObsCapabilities(appId, allowed);
  const namespace =
    deployment.k8s_namespace ?? `${projectName}-${deployment.name}`;
  const base = `/projects/${encodeURIComponent(projectName)}/apps/${appId}`;
  return (
    <AppSection allowed={allowed}>
      {caps.loading ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : caps.data?.logs ? (
        <Suspense>
          <LogsExplorer appId={appId} base={base} />
        </Suspense>
      ) : (
        <LogsView project={projectName} namespace={namespace} />
      )}
    </AppSection>
  );
}
