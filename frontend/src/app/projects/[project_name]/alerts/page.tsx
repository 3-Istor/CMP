"use client";

import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function ProjectAlertsPage() {
  const t = useTranslations("projectNav");
  const { projectName } = useProjectContext();
  return (
    <ProjectSection title={t("alerts")} capability="app.viewObservability">
      <AlertsPanel project={projectName} />
    </ProjectSection>
  );
}
