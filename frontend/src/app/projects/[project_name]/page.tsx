"use client";

import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { errorMessage } from "@/components/activity/labels";
import { AppCard } from "@/components/projects/AppCard";
import { GettingStarted } from "@/components/projects/GettingStarted";
import { MenuText } from "@/components/projects/MenuText";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { ToolsMenu } from "@/components/projects/ToolsMenu";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NotConnected } from "@/components/clusters/NotConnected";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createDeployment,
  deleteProject,
  getCatalog,
  getDeletionProtection,
  getProjectLinks,
  isNotConnected,
  setDeletionProtection,
} from "@/lib/api";
import { hasScorecard, useScorecards } from "@/lib/scorecard";
import type { CatalogTemplate, ProjectLink } from "@/types";
import {
  FolderKanban,
  Loader2,
  MoreHorizontal,
  Plus,
  Rocket,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Trash2,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

export default function ProjectPage() {
  const router = useRouter();
  const ta = useTranslations("actions");
  const tc = useTranslations("confirm");
  const t = useTranslations("projects.page");
  const tRoles = useTranslations("projects");
  const tCommon = useTranslations("common");
  const tScore = useTranslations("scorecard");
  const {
    projectName,
    role,
    can,
    apps,
    loadingApps,
    appsError,
    refreshApps,
    hasActiveDeployments,
  } = useProjectContext();

  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showUnprotectDialog, setShowUnprotectDialog] = useState(false);
  const [sortBy, setSortBy] = useState<"name" | "score">("name");
  const scores = useScorecards(
    apps.filter(hasScorecard).map((app) => app.id),
    can("app.viewLogs"),
  );
  const sortedApps = useMemo(() => {
    const byName = (a: (typeof apps)[number], b: (typeof apps)[number]) =>
      a.name.localeCompare(b.name);
    if (sortBy === "name") return [...apps].sort(byName);
    return [...apps].sort(
      (a, b) =>
        (scores[b.id]?.score ?? -1) - (scores[a.id]?.score ?? -1) ||
        byName(a, b),
    );
  }, [apps, scores, sortBy]);

  // What this user may do here. Every can() is false while the role loads,
  // so action buttons stay absent instead of flashing into view and then
  // disappearing.
  const canDeploy = can("app.deploy");
  const canDeleteProject = can("project.delete");
  const canProtect = can("project.protect");
  const canViewObservability = can("app.viewObservability");

  const [links, setLinks] = useState<ProjectLink[]>([]);
  useEffect(() => {
    if (!canViewObservability) return;
    getProjectLinks(projectName)
      .then(setLinks)
      .catch(() => setLinks([]));
  }, [projectName, canViewObservability]);

  // null until read: the toggle stays hidden rather than offer the wrong action.
  const [protectedOn, setProtectedOn] = useState<boolean | null>(null);
  useEffect(() => {
    if (!canDeleteProject) return;
    getDeletionProtection(projectName)
      .then(({ enabled }) => setProtectedOn(enabled))
      .catch(() => setProtectedOn(null));
  }, [projectName, canDeleteProject]);

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
      toast.error(ta("errors.loadTemplates"));
    } finally {
      setLoadingCatalog(false);
    }
  };

  const handleOpenCatalog = () => {
    // Guard as well as hide: the role can change between the render that drew
    // the button and the click.
    if (!canDeploy) {
      toast.error(ta("errors.notAllowed"));
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
        // CRITICAL: Set project_id at top level for database storage
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
      toast.success(ta("errors.deployStarted", { name }));
      setSelectedTemplate(null);
      setShowCatalog(false);
      refreshApps();
    } catch (err) {
      toast.error(ta("errors.deploy", { message: String(err) }));
    } finally {
      setDeploying(false);
    }
  };

  const handleDeleteProject = async () => {
    if (!canDeleteProject) throw new Error(ta("errors.notAllowed"));
    await deleteProject(projectName);
    toast.success(ta("toasts.projectDeleting", { name: projectName }));
    router.push("/");
  };

  const changeProtection = async (enabled: boolean) => {
    if (!canProtect) throw new Error(ta("errors.notAllowed"));
    const res = await setDeletionProtection(projectName, enabled);
    setProtectedOn(res.enabled);
    toast.success(
      ta(res.enabled ? "toasts.protectedOn" : "toasts.protectedOff"),
    );
  };

  const handleProtect = () =>
    changeProtection(true).catch((err) =>
      toast.error(ta("errors.protect", { message: errorMessage(err) })),
    );

  return (
    <div className="min-h-screen">
      <main className="px-6 py-8 max-w-7xl mx-auto space-y-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-primary/10 p-2.5">
              <FolderKanban className="h-6 w-6 text-primary" />
            </div>
            <div>
              <h1 className="text-2xl font-bold capitalize">{projectName}</h1>
              <p className="text-sm text-muted-foreground">
                {role
                  ? t("kubernetesRole", { role: tRoles(`roles.${role}`) })
                  : t("kubernetes")}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="font-mono text-xs">
              {t("appCount", { count: apps.length })}
            </Badge>
            {canViewObservability && <ToolsMenu links={links} />}
            {canDeploy && (
              <Button size="sm" onClick={handleOpenCatalog}>
                <Plus aria-hidden />
                {ta("newApp")}
              </Button>
            )}
            {canDeleteProject && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="icon"
                    title={ta("more")}
                    aria-label={ta("more")}
                  >
                    <MoreHorizontal aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-[16rem]">
                  {canProtect && protectedOn !== null && (
                    <>
                      <DropdownMenuItem
                        className="gap-2"
                        onClick={
                          protectedOn
                            ? () => setShowUnprotectDialog(true)
                            : handleProtect
                        }
                      >
                        {protectedOn ? (
                          <ShieldOff className="h-4 w-4" aria-hidden />
                        ) : (
                          <ShieldCheck className="h-4 w-4" aria-hidden />
                        )}
                        {ta(protectedOn ? "protectOff" : "protectOn")}
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                    </>
                  )}
                  <DropdownMenuItem
                    className="gap-2 text-destructive hover:text-destructive focus:text-destructive"
                    disabled={protectedOn === true}
                    onClick={() => setShowDeleteDialog(true)}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden />
                    <MenuText
                      label={ta("deleteProject")}
                      reason={protectedOn ? ta("reasons.protected") : null}
                    />
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {canViewObservability && (
          <SecurityAlert
            project={projectName}
            onOpen={() =>
              router.push(`/projects/${encodeURIComponent(projectName)}/security`)
            }
          />
        )}

        <Separator />

        <div className="space-y-6">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted-foreground">
                {t("description")}
              </p>
              {hasActiveDeployments && (
                <Badge
                  variant="outline"
                  className="animate-pulse bg-primary/10 text-primary border-primary/20"
                >
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                  {t("live")}
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                onClick={refreshApps}
                disabled={loadingApps}
                title={ta("refresh")}
                aria-label={ta("refresh")}
              >
                <RefreshCw
                  className={loadingApps ? "animate-spin" : undefined}
                  aria-hidden
                />
              </Button>
            </div>
          </div>

          {/* Inline catalog (shown when user clicks "Deploy App") */}
          {showCatalog && canDeploy && (
            <div className="rounded-xl border bg-card p-6 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">{t("templatesTitle")}</h3>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowCatalog(false)}
                >
                  {tCommon("close")}
                </Button>
              </div>
              {loadingCatalog ? (
                <div className="flex items-center gap-2 text-muted-foreground py-4">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("templatesLoading")}
                </div>
              ) : paasTemplates.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("noTemplates")}
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
          ) : isNotConnected(appsError) ? (
            <NotConnected cloud={isNotConnected(appsError)!.cloud} />
          ) : appsError ? (
            <div className="rounded-xl border border-destructive bg-destructive/5 p-6 text-destructive space-y-2">
              <p className="font-medium">{t("appsLoadFailed")}</p>
              <p className="text-sm opacity-80">{appsError}</p>
              <Button variant="outline" size="sm" onClick={refreshApps}>
                {tCommon("retry")}
              </Button>
            </div>
          ) : apps.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-muted-foreground">
              <Rocket className="h-9 w-9 mb-3 text-muted-foreground" />
              <p className="font-medium">{t("noApps")}</p>
              <p className="text-sm mt-1">
                {canDeploy ? t("deployFirst") : t("noAppsReadOnly")}
              </p>
              {canDeploy && (
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-4"
                  onClick={handleOpenCatalog}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  {ta("newApp")}
                </Button>
              )}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-end gap-2">
                <label
                  htmlFor="app-sort"
                  className="text-sm text-muted-foreground"
                >
                  {tScore("sortBy")}
                </label>
                <select
                  id="app-sort"
                  value={sortBy}
                  onChange={(e) =>
                    setSortBy(e.target.value as "name" | "score")
                  }
                  className="h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground"
                >
                  <option value="name">{tScore("sortName")}</option>
                  <option value="score">{tScore("sortScore")}</option>
                </select>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {sortedApps.map((app) => (
                  <AppCard key={app.id} app={app} projectName={projectName} />
                ))}
              </div>
            </>
          )}

          <GettingStarted />
        </div>
      </main>

      {/* ── Deploy modal ── */}
      <DeployModal
        template={canDeploy ? selectedTemplate : null}
        onClose={() => setSelectedTemplate(null)}
        onConfirm={handleDeploy}
        loading={deploying}
      />

      <ConfirmDialog
        open={showDeleteDialog && apps.length === 0}
        onOpenChange={setShowDeleteDialog}
        level={2}
        destructive
        title={tc("deleteProject.title", { name: projectName })}
        description={tc("deleteProject.description")}
        consequences={[
          tc("deleteProject.consequences.apps"),
          tc("deleteProject.consequences.vault"),
          tc("deleteProject.consequences.grafana"),
          tc("deleteProject.consequences.keycloak"),
          tc("deleteProject.consequences.argocd"),
        ]}
        expectedName={projectName}
        confirmLabel={tc("deleteProject.confirm")}
        onConfirm={handleDeleteProject}
      />

      <ConfirmDialog
        open={showUnprotectDialog}
        onOpenChange={setShowUnprotectDialog}
        level={1}
        title={tc("unprotect.title", { name: projectName })}
        description={tc("unprotect.description")}
        confirmLabel={tc("unprotect.confirm")}
        onConfirm={() => changeProtection(false)}
      />

      <Dialog
        open={showDeleteDialog && apps.length > 0}
        onOpenChange={setShowDeleteDialog}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {tc("projectHasApps.title", { name: projectName })}
            </DialogTitle>
            <DialogDescription>
              {tc("projectHasApps.description", { count: apps.length })}
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-1 text-sm">
            {apps.map((app) => (
              <li key={app.id}>
                <Link
                  href={`/projects/${encodeURIComponent(projectName)}/apps/${app.id}`}
                  className="font-medium text-primary hover:underline"
                  aria-label={tc("projectHasApps.open", { name: app.name })}
                >
                  {app.name}
                </Link>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(false)}>
              {tc("close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
