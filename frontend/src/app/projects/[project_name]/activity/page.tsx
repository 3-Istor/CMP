"use client";

import { ActivityTab } from "@/components/activity/ActivityTab";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function ProjectActivityPage() {
  const t = useTranslations("projectNav");
  const { projectName } = useProjectContext();
  return (
    <ProjectSection title={t("activity")} capability="app.viewObservability">
      <ActivityTab project={projectName} />
    </ProjectSection>
  );
}
