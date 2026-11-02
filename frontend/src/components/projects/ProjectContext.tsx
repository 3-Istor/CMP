"use client";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCan, useProjectApps, useProjects } from "@/lib/hooks";
import type { Capability } from "@/lib/permissions";
import type { Deployment, ProjectRole } from "@/types";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo } from "react";
import { toast } from "sonner";

const IN_PROGRESS = [
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
];

interface ProjectContextValue {
  projectName: string;
  role: ProjectRole | null;
  roleLoading: boolean;
  can: (capability: Capability) => boolean;
  apps: Deployment[];
  loadingApps: boolean;
  appsError: string | null;
  refreshApps: () => Promise<void>;
  hasActiveDeployments: boolean;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function useProjectContext(): ProjectContextValue {
  const value = useContext(ProjectContext);
  if (!value) throw new Error("useProjectContext outside ProjectProvider");
  return value;
}

export function ProjectProvider({
  projectName,
  children,
}: {
  projectName: string;
  children: React.ReactNode;
}) {
  const router = useRouter();

  const { projects } = useProjects();
  const isBeingDeleted = projects.some(
    (p) => p.name === projectName && p.status === "decommissioning",
  );

  const { role, can, loading: roleLoading } = useCan(projectName);
  const {
    apps,
    loading: loadingApps,
    error: appsError,
    refresh: refreshApps,
  } = useProjectApps(projectName);

  const hasActiveDeployments = apps.some((app) =>
    IN_PROGRESS.includes(app.status),
  );

  useEffect(() => {
    if (!hasActiveDeployments) return;
    const interval = setInterval(() => {
      refreshApps();
    }, 3000);
    return () => clearInterval(interval);
  }, [hasActiveDeployments, refreshApps]);

  useEffect(() => {
    if (
      appsError &&
      (appsError.includes("403") || appsError.includes("Forbidden"))
    ) {
      toast.error("Access denied: you are not a member of this project");
      const timer = setTimeout(() => router.push("/"), 2000);
      return () => clearTimeout(timer);
    }
  }, [appsError, router]);

  const value = useMemo(
    () => ({
      projectName,
      role,
      roleLoading,
      can,
      apps,
      loadingApps,
      appsError,
      refreshApps,
      hasActiveDeployments,
    }),
    [
      projectName,
      role,
      roleLoading,
      can,
      apps,
      loadingApps,
      appsError,
      refreshApps,
      hasActiveDeployments,
    ],
  );

  if (isBeingDeleted) {
    return (
      <div className="min-h-screen">
        <main className="px-6 py-8 max-w-3xl mx-auto">
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-center">
            <Loader2 className="h-8 w-8 animate-spin text-destructive mb-4" />
            <p className="font-medium">
              &quot;{projectName}&quot; is being deleted
            </p>
            <p className="text-sm text-muted-foreground mt-1 max-w-sm">
              Its resources are being torn down in the background. This page
              will stay unavailable until that finishes.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => router.push("/")}
            >
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to projects
            </Button>
          </div>
        </main>
      </div>
    );
  }

  return (
    <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
  );
}

export function NotAllowed() {
  const t = useTranslations("projectNav");
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-center">
      <p className="font-medium">{t("notAllowedTitle")}</p>
      <p className="text-sm text-muted-foreground mt-1 max-w-sm">
        {t("notAllowedBody")}
      </p>
    </div>
  );
}

export function ProjectSection({
  title,
  capability,
  children,
}: {
  title: string;
  capability?: Capability;
  children: React.ReactNode;
}) {
  const { can, roleLoading } = useProjectContext();
  const allowed = !capability || can(capability);

  return (
    <div className="min-h-screen">
      <main className="px-6 py-8 max-w-7xl mx-auto space-y-6">
        <h1 className="text-2xl font-bold">{title}</h1>
        {roleLoading && !allowed ? (
          <Skeleton className="h-48 w-full rounded-xl" />
        ) : allowed ? (
          children
        ) : (
          <NotAllowed />
        )}
      </main>
    </div>
  );
}
