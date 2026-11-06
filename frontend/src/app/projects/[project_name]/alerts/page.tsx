"use client";

import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import { AppFilter, useAppFilter } from "@/components/projects/AppFilter";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";
import { Suspense } from "react";

function Section() {
  const t = useTranslations("projectNav");
  const { projectName } = useProjectContext();
  const app = useAppFilter();
  return (
    <ProjectSection
      title={t("alerts")}
      capability="app.viewObservability"
      actions={<AppFilter />}
    >
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
