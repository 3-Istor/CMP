"use client";

import { EmptyState, EmptyStateButton } from "@/components/data/EmptyState";
import { useImportTasks } from "@/components/imports/ImportTasks";
import { DOCS_URL } from "@/components/layout/shellData";
import { TracesView } from "@/components/observability/TracesView";
import { NotAvailable, useObsCapabilities } from "@/components/observability/shared";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslations } from "next-intl";
import { Suspense } from "react";

export default function AppTracesPage() {
  const { deployment, appId } = useAppContext();
  const { can, projectName } = useProjectContext();
  const allowed =
    deployment.provider_type === "kubernetes" && can("app.viewObservability");
  const caps = useObsCapabilities(appId, allowed);
  const base = `/projects/${encodeURIComponent(projectName)}/apps/${appId}`;
  const t = useTranslations("imports.finish");
  const importTasks = useImportTasks();
  const empty = importTasks?.imported ? (
    <EmptyState
      title={t("noTracesTitle", { app: deployment.name })}
      reason={t("noTracesReason")}
      action={{ label: t("howToTrace"), href: `${DOCS_URL}10-observabilite/` }}
    >
      {importTasks.agent_href && (
        <EmptyStateButton action={{ label: t("assignAgent"), href: importTasks.agent_href }} />
      )}
    </EmptyState>
  ) : undefined;
  return (
    <AppSection allowed={allowed}>
      {caps.loading ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : caps.data?.traces ? (
        <Suspense>
          <TracesView appId={appId} base={base} empty={empty} />
        </Suspense>
      ) : (
        <NotAvailable signal="traces" />
      )}
    </AppSection>
  );
}
