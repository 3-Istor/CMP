"use client";

import { ProjectCard } from "@/components/projects/ProjectCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectsHealth, useSecurityOverview } from "@/lib/hooks";
import type { Deployment, Project } from "@/types";
import { FolderOpen, FolderPlus } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

export const countDeleting = (apps: Deployment[], project: string) =>
  apps.filter((a) => a.project_id === project && a.status === "deleting").length;

export function ProjectsGrid({
  projects,
  apps,
  loading,
  onCreate,
  onRetried,
}: {
  projects: Project[];
  apps: Deployment[];
  loading: boolean;
  onCreate: () => void;
  onRetried: () => void;
}) {
  const t = useTranslations("projects.home");
  const health = useProjectsHealth();
  const security = useSecurityOverview();
  if (loading && projects.length === 0) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-36 rounded-xl" />
        ))}
      </div>
    );
  }
  if (projects.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-12 text-muted-foreground">
        <FolderOpen className="mb-3 h-9 w-9" aria-hidden />
        <p className="font-medium text-foreground">{t("emptyTitle")}</p>
        <p className="mt-1 text-sm">
          {t.rich("emptyBody", {
            assistant: (chunks) => (
              <Link href="/connect-ai" className="text-primary hover:underline">
                {chunks}
              </Link>
            ),
          })}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={onCreate}>
          <FolderPlus aria-hidden />
          {t("create")}
        </Button>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {projects.map((p) => (
        <ProjectCard
          key={p.name}
          project={p}
          onRetried={onRetried}
          deletingApps={countDeleting(apps, p.name)}
          health={health[p.name]}
          grade={security.find((s) => s.project === p.name)?.grade ?? undefined}
        />
      ))}
    </div>
  );
}
