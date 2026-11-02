"use client";

import { MembersPanel } from "@/components/projects/MembersPanel";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function ProjectMembersPage() {
  const t = useTranslations("projectNav");
  const { projectName, can } = useProjectContext();
  return (
    <ProjectSection title={t("members")} capability="members.view">
      <MembersPanel
        projectName={projectName}
        canManage={can("members.manage")}
      />
    </ProjectSection>
  );
}
