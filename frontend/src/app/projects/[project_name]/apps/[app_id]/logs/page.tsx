"use client";

import { LogsView } from "@/components/activity/LogsView";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

function AppLogs() {
  const { deployment, appId } = useAppContext();
  const { projectName } = useProjectContext();
  const router = useRouter();
  const pathname = usePathname();
  const trace = useSearchParams().get("trace");
  return (
    <LogsView
      project={projectName}
      namespace={deployment.k8s_namespace ?? `${projectName}-${deployment.name}`}
      appId={appId}
      trace={trace}
      onClearTrace={() => router.replace(pathname)}
    />
  );
}

export default function AppLogsPage() {
  const { deployment } = useAppContext();
  const { can } = useProjectContext();
  const allowed = deployment.provider_type === "kubernetes" && can("app.viewLogs");
  return (
    <AppSection allowed={allowed}>
      <Suspense>
        <AppLogs />
      </Suspense>
    </AppSection>
  );
}
