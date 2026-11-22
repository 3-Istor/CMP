"use client";

import { ProjectAiTab } from "@/components/ai/ProjectAiTab";
import { ProjectSection } from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function ProjectAiPage() {
  const t = useTranslations("projectNav");
  return (
    <ProjectSection title={t("ai")}>
      <ProjectAiTab />
    </ProjectSection>
  );
}
