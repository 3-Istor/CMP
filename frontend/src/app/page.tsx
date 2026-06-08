"use client";

import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { UserNav } from "@/components/layout/UserNav";
import { CreateProjectModal } from "@/components/projects/CreateProjectModal";
import { ProjectCard } from "@/components/projects/ProjectCard";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { createDeployment, getCatalog } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import type { CatalogTemplate } from "@/types";
import { FolderPlus } from "lucide-react";
import { useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function Home() {
  const { data: session } = useSession();
  const [templates, setTemplates] = useState<CatalogTemplate[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [selectedTemplate, setSelectedTemplate] =
    useState<CatalogTemplate | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [dashboardKey, setDashboardKey] = useState(0);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);

  const { projects, loading: loadingProjects, refresh: refreshProjects } = useProjects();

  const userName =
    session?.user?.name ||
    `${session?.user?.given_name ?? ""} ${session?.user?.family_name ?? ""}`.trim() ||
    session?.user?.email ||
    "Developer";

  useEffect(() => {
    getCatalog()
      .then(setTemplates)
      .catch((err) => {
        console.error("Failed to load catalog:", err);
        toast.error("Failed to load templates from repository");
      })
      .finally(() => setLoadingCatalog(false));
  }, []);

  const paasTemplates = templates.filter((t) => t.category === "paas");
  const iaasTemplates = templates.filter((t) => t.category !== "paas");

  const handleDeploy = async (
    name: string,
    config: Record<string, string | number>,
  ) => {
    if (!selectedTemplate) return;
    setDeploying(true);
    try {
      await createDeployment({
        name,
        template_id: selectedTemplate.id,
        app_config: config,
      });
      toast.success(`Deployment of "${name}" started`);
      setSelectedTemplate(null);
      setDashboardKey((prev) => prev + 1);
    } catch (err) {
      toast.error(`Deploy failed: ${err}`);
    } finally {
      setDeploying(false);
    }
  };

  return (
    <div className="min-h-screen">
      {/* ── Header ── */}
      <header className="border-b px-6 py-4 flex items-center gap-3">
        <span className="text-2xl">⚡</span>
        <div>
          <h1 className="text-lg font-bold leading-none">CMP</h1>
          <p className="text-xs text-muted-foreground">
            Cloud Native Platform
          </p>
        </div>
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

        {/* ── Projects Section ── */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-semibold">My Projects</h2>
              <p className="text-sm text-muted-foreground mt-0.5">
                Each project is an isolated team boundary with its own
                Keycloak groups, Vault policies, and ArgoCD AppProject.
              </p>
            </div>
            <Button
              size="sm"
              onClick={() => setCreateProjectOpen(true)}
            >
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
          ) : projects.length === 0 ? (
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
              {projects.map((p) => (
                <ProjectCard key={p.name} project={p} />
              ))}
            </div>
          )}
        </section>

        <Separator />

        {/* ── App Catalog ── */}
        <section>
          <h2 className="text-xl font-semibold mb-1">App Catalog</h2>
          <p className="text-sm text-muted-foreground mb-4">
            Deploy infrastructure from Git repository templates.
          </p>
          {loadingCatalog ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
              <span className="ml-3 text-muted-foreground">
                Loading templates…
              </span>
            </div>
          ) : templates.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p className="text-lg mb-2">No templates found in repository</p>
              <p className="text-sm">
                Check that the backend is running and templates are enabled.
              </p>
            </div>
          ) : (
            <Tabs defaultValue="paas" className="w-full">
              <TabsList className="mb-4">
                <TabsTrigger value="paas" className="gap-2">
                  <span>🚀</span>
                  <span>Kubernetes & GitOps</span>
                </TabsTrigger>
                <TabsTrigger value="iaas" className="gap-2">
                  <span>🖥️</span>
                  <span>IaaS & VMs</span>
                </TabsTrigger>
              </TabsList>

              <TabsContent value="paas" className="mt-0">
                <p className="text-sm text-muted-foreground mb-4">
                  Containerised applications with GitOps (GitHub + ArgoCD)
                </p>
                {paasTemplates.length === 0 ? (
                  <div className="text-center py-12 text-muted-foreground">
                    <p>No Kubernetes templates available</p>
                  </div>
                ) : (
                  <CatalogGrid
                    templates={paasTemplates}
                    onDeploy={setSelectedTemplate}
                  />
                )}
              </TabsContent>

              <TabsContent value="iaas" className="mt-0">
                <p className="text-sm text-muted-foreground mb-4">
                  VMs on OpenStack and AWS Auto Scaling Groups
                </p>
                {iaasTemplates.length === 0 ? (
                  <div className="text-center py-12 text-muted-foreground">
                    <p>No IaaS templates available</p>
                  </div>
                ) : (
                  <CatalogGrid
                    templates={iaasTemplates}
                    onDeploy={setSelectedTemplate}
                  />
                )}
              </TabsContent>
            </Tabs>
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
        onCreated={refreshProjects}
      />

      <DeployModal
        template={selectedTemplate}
        onClose={() => setSelectedTemplate(null)}
        onConfirm={handleDeploy}
        loading={deploying}
      />
    </div>
  );
}
