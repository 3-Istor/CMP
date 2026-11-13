"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";

import { ProjectAccessReviewCard } from "@/components/access-reviews/ProjectAccessReviewCard";
import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { AppCard } from "@/components/projects/AppCard";
import { GettingStarted } from "@/components/projects/GettingStarted";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { ToolsMenu } from "@/components/projects/ToolsMenu";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NotConnected } from "@/components/clusters/NotConnected";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createDeployment,
  getCatalog,
  getProjectLinks,
  isNotConnected,
} from "@/lib/api";
import { hasScorecard, useScorecards } from "@/lib/scorecard";
import type { CatalogTemplate, ProjectLink } from "@/types";
import {
  FolderKanban,
  Loader2,
  Plus,
  Rocket,
  RefreshCw,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  useEffect,
  useMemo,
  useState,
} from "react";
import { toast } from "sonner";

export default function ProjectPage() {
  const router = useRouter();
  const ta = useTranslations("actions");
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
  const canViewObservability = can("app.viewObservability");

  const [links, setLinks] = useState<ProjectLink[]>([]);
  useEffect(() => {
    if (!canViewObservability) return;
    getProjectLinks(projectName)
      .then(setLinks)
      .catch(() => setLinks([]));
  }, [projectName, canViewObservability]);

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
      await createDeployment({
        name,
        template_id: selectedTemplate.id,
        // CRITICAL: Set project_id at top level for database storage
        project_id: projectName,
        app_config: {
          ...config,
          project_name: projectName,
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

  return (
    <div className="min-h-screen">
      <PageHeader
        title={<span className="capitalize">{projectName}</span>}
        description={
          role
            ? t("kubernetesRole", { role: tRoles(`roles.${role}`) })
            : t("kubernetes")
        }
        icon={FolderKanban}
        actions={
          <>
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
          </>
        }
      />
      <PageBody>
        {canViewObservability && (
          <SecurityAlert
            project={projectName}
            onOpen={() =>
              router.push(`/projects/${encodeURIComponent(projectName)}/security`)
            }
          />
        )}

        <div className="space-y-6">
          {role === "owner" || role === "admin" ? (
            <ProjectAccessReviewCard project={projectName} />
          ) : null}
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
              <p className="text-sm">{appsError}</p>
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
      </PageBody>

      {/* ── Deploy modal ── */}
      <DeployModal
        template={canDeploy ? selectedTemplate : null}
        onClose={() => setSelectedTemplate(null)}
        onConfirm={handleDeploy}
        loading={deploying}
      />

    </div>
  );
}
