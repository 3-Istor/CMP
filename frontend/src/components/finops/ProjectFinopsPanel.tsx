"use client";

import { OpenDashboardButton } from "@/components/ui/open-dashboard-button";
import { useTranslations } from "next-intl";
import { FinopsOverviewDashboard } from "./FinopsOverviewDashboard";

/** The full FinOps dashboard of one project, for the project page's FinOps tab. */
export function ProjectFinopsPanel({ project }: { project: string }) {
  const t = useTranslations("finops");
  return (
    <FinopsOverviewDashboard
      fixedProject={project}
      actions={
        <OpenDashboardButton
          href={`/finops?project=${encodeURIComponent(project)}`}
          label={t("projectPanel.openDashboard")}
        />
      }
    />
  );
}
