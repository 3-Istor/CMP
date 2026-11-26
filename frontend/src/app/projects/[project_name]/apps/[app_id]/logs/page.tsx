"use client";

import { LogsView } from "@/components/activity/LogsView";
import { logsQueryFromParams } from "@/components/activity/logsQuery";
import { useAppContext, useAppEnv } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

function AppLogs() {
  const { deployment, appId } = useAppContext();
  const { projectName } = useProjectContext();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const trace = search.get("trace");
  const env = useAppEnv();
  const namespace = deployment.k8s_namespace ?? `${projectName}-${deployment.name}`;
  return (
    <LogsView
      project={projectName}
      namespace={env === "staging" ? `${namespace}-stg` : namespace}
      appId={appId}
      env={env}
      trace={trace}
      initialQuery={logsQueryFromParams(search)}
      onClearTrace={() => router.replace(env === "staging" ? `${pathname}?env=staging` : pathname)}
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
