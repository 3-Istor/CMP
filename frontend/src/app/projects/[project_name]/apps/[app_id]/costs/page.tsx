"use client";

import { ProjectFinopsPanel } from "@/components/finops/ProjectFinopsPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";

export default function AppCostsPage() {
  const t = useTranslations("apps.costs");
  const { projectName } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection allowed={can("finops.view")}>
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {t("projectLevel")}
        </p>
        <ProjectFinopsPanel project={projectName} />
      </div>
    </AppSection>
  );
}
