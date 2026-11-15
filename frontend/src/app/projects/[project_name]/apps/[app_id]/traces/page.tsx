"use client";

import { TracesView } from "@/components/observability/TracesView";
import { NotAvailable, useObsCapabilities } from "@/components/observability/shared";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Skeleton } from "@/components/ui/skeleton";
import { Suspense } from "react";

export default function AppTracesPage() {
  const { deployment, appId } = useAppContext();
  const { can, projectName } = useProjectContext();
  const allowed =
    deployment.provider_type === "kubernetes" && can("app.viewObservability");
  const caps = useObsCapabilities(appId, allowed);
  const base = `/projects/${encodeURIComponent(projectName)}/apps/${appId}`;
  return (
    <AppSection allowed={allowed}>
      {caps.loading ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : caps.data?.traces ? (
        <Suspense>
          <TracesView appId={appId} base={base} />
        </Suspense>
      ) : (
        <NotAvailable signal="traces" />
      )}
    </AppSection>
  );
}
