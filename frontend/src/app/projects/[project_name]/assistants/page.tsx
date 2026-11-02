"use client";

import { ProjectAgentPanel } from "@/components/agents/ProjectAgentPanel";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function ProjectAssistantsPage() {
  const t = useTranslations("projectNav");
  const { projectName, can } = useProjectContext();
  return (
    <ProjectSection title={t("assistants")}>
      <ProjectAgentPanel
        project={projectName}
        canConfigure={can("agents.configure")}
      />
    </ProjectSection>
  );
}
