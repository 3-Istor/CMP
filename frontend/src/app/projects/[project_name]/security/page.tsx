"use client";

import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { SecurityDashboard } from "@/components/security/SecurityDashboard";
import { useTranslations } from "next-intl";

export default function ProjectSecurityPage() {
  const t = useTranslations("projectNav");
  const { projectName } = useProjectContext();
  return (
    <ProjectSection title={t("security")} capability="app.viewObservability">
      <SecurityDashboard
        project={projectName}
        fullPageLink
        showSettings={false}
      />
    </ProjectSection>
  );
}
