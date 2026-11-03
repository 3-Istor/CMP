"use client";

import { ProjectProvider } from "@/components/projects/ProjectContext";
import { useParams } from "next/navigation";

export default function ProjectLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const params = useParams();
  return (
    <ProjectProvider projectName={params.project_name as string}>
      {children}
    </ProjectProvider>
  );
}
