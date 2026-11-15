"use client";

import { ImportWizard } from "@/components/imports/ImportWizard";
import { ProjectSection } from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";
import { Suspense } from "react";

export default function ProjectImportPage() {
  const t = useTranslations("imports");
  return (
    <ProjectSection title={t("title")} capability="import.manage">
      <Suspense>
        <ImportWizard />
      </Suspense>
    </ProjectSection>
  );
}
