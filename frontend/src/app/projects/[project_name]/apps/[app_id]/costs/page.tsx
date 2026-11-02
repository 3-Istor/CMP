"use client";

import { ProjectFinopsPanel } from "@/components/finops/ProjectFinopsPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";

export default function AppCostsPage() {
  const { projectName } = useAppContext();
  const { can } = useProjectContext();
  return (
    <AppSection allowed={can("finops.view")}>
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Les coûts sont suivis au niveau du projet.
        </p>
        <ProjectFinopsPanel project={projectName} />
      </div>
    </AppSection>
  );
}
