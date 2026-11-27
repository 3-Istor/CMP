"use client";

import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import { AlertsView } from "@/components/observability/AlertsView";
import { useObsCapabilities } from "@/components/observability/shared";
import { AppFilter, useAppFilter } from "@/components/projects/AppFilter";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function Section() {
  const t = useTranslations("projectNav");
  const { projectName } = useProjectContext();
  const app = useAppFilter();
  const silence = useSearchParams().get("silence");
  const caps = useObsCapabilities(app?.id ?? 0, Boolean(app));
  return (
    <ProjectSection
      title={t("alerts")}
      capability="alerts.view"
      actions={<AppFilter />}
    >
      {app && caps.data?.alerts && (
        <div className="mb-6">
          <AlertsView
            key={silence ?? ""}
            appId={app.id}
            project={projectName}
            app={app.name}
            maxHours={caps.data.silence_max_hours}
            initialSilence={silence}
          />
        </div>
      )}
      <AlertsPanel
        key={app?.id ?? "all"}
        project={projectName}
        app={app?.name}
      />
    </ProjectSection>
  );
}

export default function ProjectAlertsPage() {
  return (
    <Suspense>
      <Section />
    </Suspense>
  );
}
