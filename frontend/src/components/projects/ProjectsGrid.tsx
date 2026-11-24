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
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="space-y-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10" aria-hidden>
            <div className="flex items-start justify-between">
              <Skeleton className="size-10 rounded-lg" />
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
            <Skeleton className="h-5 w-2/3" />
            <div className="flex gap-2">
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-4 w-24" />
            </div>
            <Skeleton className="h-1.5 w-full rounded-full" />
          </div>
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
          grade={security.find((s) => s.project === p.name)?.grade}
        />
      ))}
    </div>
  );
}
