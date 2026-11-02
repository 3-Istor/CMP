"use client";

import { AssistantCard } from "@/components/agents/AssistantCard";
import Link from "next/link";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { FavoritesSection } from "@/components/favorites/FavoritesSection";
import { GlobalInfraHealth } from "@/components/dashboard/GlobalInfraHealth";
import { CreateProjectModal } from "@/components/projects/CreateProjectModal";
import { ProjectCard } from "@/components/projects/ProjectCard";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjects } from "@/lib/hooks";
import { isProjectInFlight } from "@/types";
import { FolderOpen, FolderPlus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useSession } from "next-auth/react";
import { useCallback, useEffect, useState } from "react";

export default function Home() {
  const { data: session } = useSession();
  const t = useTranslations("shell.home");
  const tProjects = useTranslations("projects.home");
  const tApps = useTranslations("apps.home");
  const [dashboardKey] = useState(0);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const {
    projects,
    loading: loadingProjects,
    refresh: refreshProjects,
  } = useProjects();

  // The backend records the project as soon as it accepts the request, then
  // updates its status at every bootstrap or teardown step, so the list itself
  // says what is going on.
  const handleProjectCreated = useCallback(() => {
    refreshProjects();
  }, [refreshProjects]);

  const hasProjectInFlight = projects.some(isProjectInFlight);

  useEffect(() => {
    if (!hasProjectInFlight) return;
    const interval = setInterval(refreshProjects, 3000);
    return () => clearInterval(interval);
  }, [hasProjectInFlight, refreshProjects]);

  const firstName =
    session?.user?.given_name ||
    session?.user?.name?.split(" ")[0] ||
    session?.user?.email?.split("@")[0] ||
    t("fallbackName");

  return (
    <div className="min-h-screen">
      <main className="px-6 py-8 space-y-10 max-w-7xl mx-auto">
        <h1 className="text-2xl font-semibold">
          {t("greeting", { name: firstName })}
        </h1>

        <AssistantCard />

        <GlobalInfraHealth />

        <FavoritesSection onRetried={refreshProjects} />

        {/* ── Projects Section ── */}
        <section id="projects" className="scroll-mt-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-semibold">{tProjects("title")}</h2>
              <p className="text-sm text-muted-foreground mt-0.5">
                {tProjects("subtitle")}
              </p>
            </div>
            <Button size="sm" onClick={() => setCreateProjectOpen(true)}>
              <FolderPlus className="mr-2 h-4 w-4" />
              {tProjects("new")}
            </Button>
          </div>

          {loadingProjects ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-36 rounded-xl" />
              ))}
            </div>
          ) : projects.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-12 text-muted-foreground">
              <FolderOpen className="h-9 w-9 mb-3 text-muted-foreground" />
              <p className="font-medium">{tProjects("emptyTitle")}</p>
              <p className="text-sm mt-1">
                {tProjects.rich("emptyBody", {
                  assistant: (chunks) => (
                    <Link
                      href="/connect-ai"
                      className="text-primary hover:underline"
                    >
                      {chunks}
                    </Link>
                  ),
                })}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setCreateProjectOpen(true)}
              >
                <FolderPlus className="mr-2 h-4 w-4" />
                {tProjects("create")}
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {projects.map((p) => (
                <ProjectCard
                  key={p.name}
                  project={p}
                  onRetried={refreshProjects}
                />
              ))}
            </div>
          )}
        </section>

        <Separator />

        {/* ── All Recent Deployments ── */}
        <section id="applications" className="scroll-mt-6">
          <h2 className="text-xl font-semibold mb-4">{tApps("allDeployments")}</h2>
          <Dashboard key={dashboardKey} />
        </section>
      </main>

      {/* ── Modals ── */}
      <CreateProjectModal
        open={createProjectOpen}
        onClose={() => setCreateProjectOpen(false)}
        onCreated={handleProjectCreated}
      />
    </div>
  );
}
