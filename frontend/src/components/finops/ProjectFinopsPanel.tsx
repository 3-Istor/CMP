"use client";

import { OpenDashboardButton } from "@/components/ui/open-dashboard-button";
import { FinopsOverviewDashboard } from "./FinopsOverviewDashboard";

/** The full FinOps dashboard of one project, for the project page's FinOps tab. */
export function ProjectFinopsPanel({ project }: { project: string }) {
  return (
    <FinopsOverviewDashboard
      fixedProject={project}
      actions={
        <OpenDashboardButton
          href={`/finops?project=${encodeURIComponent(project)}`}
          label="Ouvrir le dashboard FinOps"
        />
      }
    />
  );
}
