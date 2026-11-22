"use client";

import { CreateAgentWizard } from "@/components/ai/CreateAgentWizard";
import { ProjectSection } from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function NewAgentPage() {
  const t = useTranslations("ai.wizard");
  return (
    <ProjectSection title={t("pageTitle")} capability="agent.manage">
      <CreateAgentWizard />
    </ProjectSection>
  );
}
