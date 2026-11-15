"use client";

import { ProjectSection } from "@/components/projects/ProjectContext";
import { TopologyView } from "@/components/topology/TopologyView";
import { useTranslations } from "next-intl";

export default function ProjectTopologyPage() {
  const t = useTranslations("projectNav");
  return (
    <ProjectSection title={t("topology")} capability="topology.view">
      <TopologyView />
    </ProjectSection>
  );
}
