"use client";

import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { UserNav } from "@/components/layout/UserNav";
import { AppCard } from "@/components/projects/AppCard";
import { MembersPanel } from "@/components/projects/MembersPanel";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { createDeployment, deleteProject, getCatalog } from "@/lib/api";
import { useCan, useProjectApps } from "@/lib/hooks";
import { addPendingDeletion, usePendingDeletions } from "@/lib/pendingDeletions";
import { ROLE_LABEL } from "@/lib/permissions";
import type { CatalogTemplate } from "@/types";
import {
  Activity,
  AlarmClock,
  ArrowLeft,
  ExternalLink,
  FileCode,
  FolderKanban,
  LayoutGrid,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  Users,
  Vault,
  Wallet
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

  // If this project is already being torn down (e.g. reached via a stale
  // tab or the back button after a delete elsewhere), don't render the
  // normal admin UI — the Keycloak groups backing project access can
  // disappear mid-render.
  const pendingDeletions = usePendingDeletions();
  const isBeingDeleted = pendingDeletions.some((d) => d.name === projectName);

  // What this user may do here. Every can() is false while the role loads,
  // so action buttons stay absent instead of flashing into view and then
  // disappearing. A project mid-teardown needs no separate guard: the
  // isBeingDeleted branch below returns before any of this is rendered.
  const { role, can } = useCan(projectName);
  const canDeploy = can("app.deploy");
  const canDeleteProject = can("project.delete");
  const canViewMembers = can("members.view");
  const canViewObservability = can("app.viewObservability");

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
    // Guard as well as hide: the role can change between the render that drew
    // the button and the click.
    if (!canDeploy) {
      toast.error("Only project admins can deploy applications");
      return;
    }
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
    if (!canDeleteProject) {
      toast.error("Only project admins can delete a project");
      setShowDeleteDialog(false);
      return;
    }

    if (apps.length > 0) {
      toast.error(
        `Cannot delete project: ${apps.length} active application(s). Delete all apps first.`,
      );
      setShowDeleteDialog(false);
      return;
    }

    setDeleting(true);
    try {
      await deleteProject(projectName);

      // The call only schedules teardown — Keycloak group removal (which
      // the project list is derived from) happens in the background and
      // can take a while, so mark it as deleting rather than claim it's
      // already gone.
      addPendingDeletion(projectName);
      toast.success(`Deletion of "${projectName}" started`);
      router.push("/");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`Failed to delete project: ${msg}`);
    } finally {
      setDeleting(false);
      setShowDeleteDialog(false);
    }
  };

  if (isBeingDeleted) {
    return (
      <div className="min-h-screen">
        <header className="border-b px-6 py-4 flex items-center gap-3">
          <div className="ml-auto">
            <UserNav />
          </div>
        </header>
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
    <div className="min-h-screen">
      {/* ── Header ── */}
      <header className="border-b px-6 py-4 flex items-center gap-3">
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
                  {role ? ` · ${ROLE_LABEL[role]}` : ""}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  router.push(
                    `/finops?project=${encodeURIComponent(projectName)}`,
                  )
                }
              >
                <Wallet className="mr-2 h-4 w-4" />
                Voir détails FinOps
              </Button>
              <Badge variant="outline" className="font-mono text-xs">
                {apps.length} app{apps.length !== 1 ? "s" : ""}
              </Badge>
            </div>
          </div>
        </div>

        <Separator />

        {/* ── Project quick links ──
            Hidden from guests: these are the observability and secrets
            surfaces, and a guest who is not allowed to read logs has no
            business in Grafana, Vault or the status page either. */}
        {canViewObservability && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Project Links</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {/* ArgoCD bootstrap */}
            <a
              href={`https://argocd.3istor.com/applications/argocd/${projectName}-bootstrap?resource=`}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ variant: "outline", size: "sm", className: "justify-start" })}
            >
              <FileCode className="mr-2 h-4 w-4" />
              ArgoCD
              <ExternalLink className="ml-auto h-3 w-3" />
            </a>

            {/* Offhours */}
            <a
              href={`https://offhours-${projectName}.3istor.com/`}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ variant: "outline", size: "sm", className: "justify-start" })}
            >
              <AlarmClock className="mr-2 h-4 w-4" />
              Offhours
              <ExternalLink className="ml-auto h-3 w-3" />
            </a>

            {/* Vault — admin only: a deep link that 403s inside Vault is
                worse than no link at all. */}
            {can("app.viewSecretsLink") && (
              <a
                href={`https://vault.3istor.com/ui/vault/secrets-engines/project-${encodeURIComponent(projectName)}/kv/list`}
                target="_blank"
                rel="noopener noreferrer"
                className={buttonVariants({ variant: "outline", size: "sm", className: "justify-start" })}
              >
                <Vault className="mr-2 h-4 w-4" />
                Vault
                <ExternalLink className="ml-auto h-3 w-3" />
              </a>
            )}

            {/* Gatus / Status */}
            <a
              href={`https://status-${projectName}.3istor.com/`}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ variant: "outline", size: "sm", className: "justify-start" })}
            >
              <Activity className="mr-2 h-4 w-4" />
              Status
              <ExternalLink className="ml-auto h-3 w-3" />
            </a>
          </CardContent>
        </Card>
        )}

        {/* ── Tabs ── */}
        <Tabs defaultValue="apps">
          <TabsList>
            <TabsTrigger value="apps" className="gap-2">
              <LayoutGrid className="h-4 w-4" />
              Applications
            </TabsTrigger>
            {/* Guests don't get the roster — who else works here is not
                theirs to see, and the API would 403 the fetch anyway. */}
            {canViewMembers && (
              <TabsTrigger value="members" className="gap-2">
                <Users className="h-4 w-4" />
                Members
              </TabsTrigger>
            )}
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
                {canDeploy && (
                  <Button size="sm" onClick={handleOpenCatalog}>
                    <Plus className="mr-2 h-4 w-4" />
                    Deploy App
                  </Button>
                )}
                {canDeleteProject && (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => setShowDeleteDialog(true)}
                    disabled={apps.length > 0}
                    title={
                      apps.length > 0
                        ? `Delete all ${apps.length} app(s) first`
                        : "Delete this project"
                    }
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    Delete Project
                  </Button>
                )}
              </div>
            </div>

            {/* Inline catalog (shown when user clicks "Deploy App") */}
            {showCatalog && canDeploy && (
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
                    canDeploy={canDeploy}
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
                  {canDeploy
                    ? "Deploy your first app to this project."
                    : "Nothing has been deployed here yet."}
                </p>
                {canDeploy && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-4"
                    onClick={handleOpenCatalog}
                  >
                    <Plus className="mr-2 h-4 w-4" />
                    Deploy App
                  </Button>
                )}
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
          {canViewMembers && (
            <TabsContent value="members" className="mt-6 space-y-6">
              <MembersPanel
                projectName={projectName}
                canManage={can("members.manage")}
              />
            </TabsContent>
          )}
        </Tabs>
      </main>

      {/* ── Deploy modal ── */}
      <DeployModal
        template={canDeploy ? selectedTemplate : null}
        onClose={() => setSelectedTemplate(null)}
        onConfirm={handleDeploy}
        loading={deploying}
      />

      <Dialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Project &quot;{projectName}&quot;?</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>This will permanently delete:</p>
            <ul className="list-disc list-inside text-sm space-y-1 ml-2">
              <li>Keycloak groups (admins, members & guests)</li>
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
