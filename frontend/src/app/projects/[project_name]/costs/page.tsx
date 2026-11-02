"use client";

import { ProjectFinopsPanel } from "@/components/finops/ProjectFinopsPanel";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function ProjectCostsPage() {
  const t = useTranslations("projectNav");
  const { projectName } = useProjectContext();
  return (
    <ProjectSection title={t("costs")} capability="finops.view">
      <ProjectFinopsPanel project={projectName} />
    </ProjectSection>
  );
}
