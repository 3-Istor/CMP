"use client";

import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { UserNav } from "@/components/layout/UserNav";
import { AppCard } from "@/components/projects/AppCard";
import { MembersPanel } from "@/components/projects/MembersPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { createDeployment, getCatalog } from "@/lib/api";
import { useProjectApps } from "@/lib/hooks";
import type { CatalogTemplate } from "@/types";
import {
    ArrowLeft,
    FolderKanban,
    LayoutGrid,
    Loader2,
    Plus,
    RefreshCw,
    Trash2,
    Users,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function ProjectPage() {
  const params = useParams();
  const router = useRouter();
  const projectName = params.project_name as string;

  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const {
    apps,
    loading: loadingApps,
    error: appsError,
    refresh: refreshApps,
  } = useProjectApps(projectName);

  // Auto-refresh apps while any deployment is in progress
  useEffect(() => {
    const hasActiveDeployment = apps.some((app) =>
      ["pending", "initializing", "planning", "deploying", "deleting"].includes(
        app.status,
      ),
    );

    if (hasActiveDeployment) {
      const interval = setInterval(() => {
        refreshApps();
      }, 3000); // Poll every 3 seconds

      return () => clearInterval(interval);
    }
  }, [apps, refreshApps]);

  const hasActiveDeployments = apps.some((app) =>
    ["pending", "initializing", "planning", "deploying", "deleting"].includes(
      app.status,
    ),
  );

  // Redirect if access denied (403)
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

  // Catalog for deploy modal
  const [templates, setTemplates] = useState<CatalogTemplate[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [selectedTemplate, setSelectedTemplate] =
    useState<CatalogTemplate | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);

  const paasTemplates = templates.filter((t) => t.category === "paas");

  const loadCatalog = async () => {
    if (templates.length > 0) return; // Already loaded
    setLoadingCatalog(true);
    try {
      const data = await getCatalog();
      setTemplates(data);
    } catch {
      toast.error("Failed to load templates");
    } finally {
      setLoadingCatalog(false);
    }
  };

  const handleOpenCatalog = () => {
    setShowCatalog(true);
    loadCatalog();
  };

  const handleDeploy = async (
    name: string,
    config: Record<string, string | number>,
  ) => {
    if (!selectedTemplate) return;
    setDeploying(true);
    try {
      // Fetch GitHub installation ID from user profile
      const { github_installation_id } = await import("@/lib/api").then((m) =>
        m.getGitHubStatus(),
      );

      await createDeployment({
        name,
        template_id: selectedTemplate.id,
        // 🔑 CRITICAL: Set project_id at top level for database storage
        project_id: projectName,
        // Inject project context AND GitHub installation ID
        app_config: {
          ...config,
          project_name: projectName,
          // Only add github_installation_id if template is Kubernetes (paas)
          ...(selectedTemplate.category === "paas" && github_installation_id
            ? { github_installation_id: String(github_installation_id) }
            : {}),
        },
      });
      toast.success(`Deployment of "${name}" started`);
      setSelectedTemplate(null);
      setShowCatalog(false);
      refreshApps();
    } catch (err) {
      toast.error(`Deploy failed: ${err}`);
    } finally {
      setDeploying(false);
    }
  };

  const handleDeleteProject = async () => {
    if (apps.length > 0) {
      toast.error(
        `Cannot delete project: ${apps.length} active application(s). Delete all apps first.`,
      );
      setShowDeleteDialog(false);
      return;
    }

    setDeleting(true);
    try {
      const response = await fetch(`/api/projects/${projectName}`, {
        method: "DELETE",
        credentials: "include",
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to delete project");
      }

      toast.success(`Project "${projectName}" deleted successfully`);
      router.push("/");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`Failed to delete project: ${msg}`);
    } finally {
      setDeleting(false);
      setShowDeleteDialog(false);
    }
  };

  return (
    <div className="min-h-screen">
      {/* ── Header ── */}
      <header className="border-b px-6 py-4 flex items-center gap-3">
        <span className="text-2xl">⚡</span>
        <div>
          <h1 className="text-lg font-bold leading-none">CMP</h1>
          <p className="text-xs text-muted-foreground">Cloud Native Platform</p>
        </div>
        <div className="ml-auto">
          <UserNav />
        </div>
      </header>

      <main className="px-6 py-8 max-w-7xl mx-auto space-y-6">
        {/* ── Breadcrumb & title ── */}
        <div className="space-y-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/")}
            className="-ml-2"
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            All Projects
          </Button>

          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              <div className="rounded-lg bg-primary/10 p-2.5">
                <FolderKanban className="h-6 w-6 text-primary" />
              </div>
              <div>
                <h2 className="text-2xl font-bold capitalize">{projectName}</h2>
                <p className="text-sm text-muted-foreground">
                  Kubernetes project
                </p>
              </div>
            </div>
            <Badge variant="outline" className="font-mono text-xs">
              {apps.length} app{apps.length !== 1 ? "s" : ""}
            </Badge>
          </div>
        </div>

        <Separator />

        {/* ── Tabs ── */}
        <Tabs defaultValue="apps">
          <TabsList>
            <TabsTrigger value="apps" className="gap-2">
              <LayoutGrid className="h-4 w-4" />
              Applications
            </TabsTrigger>
            <TabsTrigger value="members" className="gap-2">
              <Users className="h-4 w-4" />
              Members
            </TabsTrigger>
          </TabsList>

          {/* ── Applications tab ── */}
          <TabsContent value="apps" className="mt-6 space-y-6">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <p className="text-sm text-muted-foreground">
                  Applications deployed in this project.
                </p>
                {hasActiveDeployments && (
                  <Badge
                    variant="outline"
                    className="animate-pulse bg-primary/10 text-primary border-primary/20"
                  >
                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                    Live updates
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={refreshApps}
                  disabled={loadingApps}
                >
                  <RefreshCw
                    className={`h-4 w-4 ${loadingApps ? "animate-spin" : ""}`}
                  />
                </Button>
                <Button size="sm" onClick={handleOpenCatalog}>
                  <Plus className="mr-2 h-4 w-4" />
                  Deploy App
                </Button>
              </div>
            </div>

            {/* Inline catalog (shown when user clicks "Deploy App") */}
            {showCatalog && (
              <div className="rounded-xl border bg-card p-6 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">
                    Select a Kubernetes template
                  </h3>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowCatalog(false)}
                  >
                    Close
                  </Button>
                </div>
                {loadingCatalog ? (
                  <div className="flex items-center gap-2 text-muted-foreground py-4">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading templates…
                  </div>
                ) : paasTemplates.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No Kubernetes templates available.
                  </p>
                ) : (
                  <CatalogGrid
                    templates={paasTemplates}
                    onDeploy={(t) => {
                      setSelectedTemplate(t);
                      setShowCatalog(false);
                    }}
                  />
                )}
              </div>
            )}

            {/* Apps grid */}
            {loadingApps ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {[1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-40 rounded-xl" />
                ))}
              </div>
            ) : appsError ? (
              <div className="rounded-xl border border-destructive bg-destructive/5 p-6 text-destructive space-y-2">
                <p className="font-medium">Failed to load applications</p>
                <p className="text-sm opacity-80">{appsError}</p>
                <Button variant="outline" size="sm" onClick={refreshApps}>
                  Retry
                </Button>
              </div>
            ) : apps.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-muted-foreground">
                <span className="text-4xl mb-3">🚀</span>
                <p className="font-medium">No applications yet</p>
                <p className="text-sm mt-1">
                  Deploy your first app to this project.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-4"
                  onClick={handleOpenCatalog}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Deploy App
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {apps.map((app) => (
                  <AppCard key={app.id} app={app} projectName={projectName} />
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── Members tab ── */}
          <TabsContent value="members" className="mt-6 space-y-6">
            <MembersPanel projectName={projectName} />

            {/* ── Delete Project Section ── */}
            <div className="rounded-xl border border-destructive/50 bg-destructive/5 p-6 space-y-3">
              <div className="flex items-start gap-3">
                <div className="rounded-lg bg-destructive/10 p-2">
                  <Trash2 className="h-5 w-5 text-destructive" />
                </div>
                <div className="flex-1 space-y-1">
                  <h3 className="font-semibold text-destructive">
                    Danger Zone
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    Delete this project permanently. This action cannot be
                    undone.
                  </p>
                  {apps.length > 0 && (
                    <p className="text-sm text-destructive font-medium mt-2">
                      ⚠️ You must delete all {apps.length} application
                      {apps.length !== 1 ? "s" : ""} before deleting this
                      project.
                    </p>
                  )}
                </div>
              </div>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setShowDeleteDialog(true)}
                disabled={apps.length > 0}
                className="w-full sm:w-auto"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete Project
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </main>

      {/* ── Deploy modal ── */}
      <DeployModal
        template={selectedTemplate}
        onClose={() => setSelectedTemplate(null)}
        onConfirm={handleDeploy}
        loading={deploying}
      />

      <Dialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Project "{projectName}"?</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>This will permanently delete:</p>
            <ul className="list-disc list-inside text-sm space-y-1 ml-2">
              <li>Keycloak groups (admins & members)</li>
              <li>Vault policies</li>
              <li>ArgoCD AppProject</li>
            </ul>
            <p className="font-semibold text-destructive pt-2">
              This action cannot be undone.
            </p>
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={() => setShowDeleteDialog(false)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteProject}
              disabled={deleting}
            >
              {deleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                <>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete Project
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
