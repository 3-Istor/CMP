"use client";

import { Dashboard } from "@/components/dashboard/Dashboard";
import { GlobalInfraHealth } from "@/components/dashboard/GlobalInfraHealth";
import { UserNav } from "@/components/layout/UserNav";
import { CreateProjectModal } from "@/components/projects/CreateProjectModal";
import { PendingProjectCard } from "@/components/projects/PendingProjectCard";
import { ProjectCard } from "@/components/projects/ProjectCard";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjects } from "@/lib/hooks";
import { FolderPlus } from "lucide-react";
import { useSession } from "next-auth/react";
import { useCallback, useEffect, useState } from "react";

export default function Home() {
  const { data: session } = useSession();
  const [dashboardKey] = useState(0);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  // Projects that were just created and are still being bootstrapped — shown
  // optimistically with a loading bar until they appear in the real list.
  const [pendingProjects, setPendingProjects] = useState<string[]>([]);

  const {
    projects,
    loading: loadingProjects,
    refresh: refreshProjects,
  } = useProjects();

  const handleProjectCreated = useCallback(
    (projectName: string) => {
      setPendingProjects((prev) =>
        prev.includes(projectName) ? prev : [...prev, projectName],
      );
      refreshProjects();
    },
    [refreshProjects],
  );

  // Only render placeholders that aren't already in the real list. Once a
  // pending project appears in `projects`, it drops out of this derived list
  // which both hides its placeholder and stops the polling effect below.
  const visiblePending = pendingProjects.filter(
    (name) => !projects.some((p) => p.name === name),
  );

  // Poll the project list while any creation is still in flight.
  useEffect(() => {
    if (visiblePending.length === 0) return;
    const interval = setInterval(refreshProjects, 3000);
    return () => clearInterval(interval);
  }, [visiblePending.length, refreshProjects]);

  const userName =
    session?.user?.name ||
    `${session?.user?.given_name ?? ""} ${session?.user?.family_name ?? ""}`.trim() ||
    session?.user?.email ||
    "Developer";

  return (
    <div className="min-h-screen">
      {/* ── Header ── */}
      <header className="border-b px-6 py-4 flex items-center gap-3">
        <div className="ml-auto flex items-center gap-3">
          {session?.user && (
            <span className="hidden sm:block text-sm text-muted-foreground">
              Welcome,{" "}
              <span className="font-medium text-foreground">{userName}</span>
            </span>
          )}
          <UserNav />
        </div>
      </header>

      <main className="px-6 py-8 space-y-10 max-w-7xl mx-auto">
        <GlobalInfraHealth />

        {/* ── Projects Section ── */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-semibold">My Projects</h2>
              <p className="text-sm text-muted-foreground mt-0.5">
                Each project is an isolated team boundary with its own Keycloak
                groups, Vault policies, and ArgoCD AppProject.
              </p>
            </div>
            <Button size="sm" onClick={() => setCreateProjectOpen(true)}>
              <FolderPlus className="mr-2 h-4 w-4" />
              New Project
            </Button>
          </div>

          {loadingProjects ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-36 rounded-xl" />
              ))}
            </div>
          ) : projects.length === 0 && visiblePending.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-12 text-muted-foreground">
              <span className="text-4xl mb-3">🗂️</span>
              <p className="font-medium">No projects yet</p>
              <p className="text-sm mt-1">
                Create your first project to start deploying applications.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setCreateProjectOpen(true)}
              >
                <FolderPlus className="mr-2 h-4 w-4" />
                Create Project
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {visiblePending.map((name) => (
                <PendingProjectCard key={`pending-${name}`} name={name} />
              ))}
              {projects.map((p) => (
                <ProjectCard key={p.name} project={p} />
              ))}
            </div>
          )}
        </section>

        <Separator />

        {/* ── All Recent Deployments ── */}
        <section>
          <h2 className="text-xl font-semibold mb-4">All My Deployments</h2>
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
