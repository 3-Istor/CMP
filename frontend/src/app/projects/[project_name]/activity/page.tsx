"use client";

import { ActivityTab } from "@/components/activity/ActivityTab";
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
      title={t("activity")}
      capability="app.viewObservability"
      actions={<AppFilter />}
    >
      <ActivityTab
        key={app?.id ?? "all"}
        project={projectName}
        app={app?.name}
      />
    </ProjectSection>
  );
}

export default function ProjectActivityPage() {
  return (
    <Suspense>
      <Section />
    </Suspense>
  );
}
