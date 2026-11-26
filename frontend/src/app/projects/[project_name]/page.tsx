"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";

import { ProjectAccessReviewCard } from "@/components/access-reviews/ProjectAccessReviewCard";
import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { ImportsList } from "@/components/imports/ImportsList";
import { AppsList } from "@/components/projects/AppsList";
import { STATUS_TEXT, StatusDot } from "@/components/app-overview/shared";
import { GettingStarted } from "@/components/projects/GettingStarted";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { PlacementPanel } from "@/components/placement/PlacementPanel";
import { ProjectClusterCard } from "@/components/cluster/ProjectClusterCard";
import { MoveBanner } from "@/components/projects/ProjectLocation";
import { ToolsMenu } from "@/components/projects/ToolsMenu";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { NotConnected } from "@/components/clusters/NotConnected";
import {
  createDeployment,
  getCatalog,
  getProjectAppsHealth,
  getProjectLinks,
  isNotConnected,
} from "@/lib/api";
import { attentionCount } from "@/lib/appHealth";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { CatalogTemplate, ProjectLink } from "@/types";
import {
  ExternalLink,
  FolderKanban,
  Loader2,
  Plus,
  Rocket,
  ScrollText,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function ProjectPage() {
  const router = useRouter();
  const ta = useTranslations("actions");
  const t = useTranslations("projects.page");
  const tNav = useTranslations("projectNav");
  const tRoles = useTranslations("projects");
  const tCommon = useTranslations("common");
  const th = useTranslations("projectHome");
  const {
    projectName,
    role,
    targetCloud,
    can,
    apps,
    loadingApps,
    appsError,
    refreshApps,
    hasActiveDeployments,
  } = useProjectContext();

  const health = useCardData(
    `apps-health-${projectName}`,
    () => getProjectAppsHealth(projectName, "24h", 25_000),
    { enabled: role !== null, intervalMs: 30_000 },
  );
  const healthLoading = role === null || (health.loading && !health.data);
  const attention = health.data ? attentionCount(health.data.apps) : 0;
  const liveApps = apps.filter((a) => a.status !== "deleted");

  // What this user may do here. Every can() is false while the role loads,
  // so action buttons stay absent instead of flashing into view and then
  // disappearing.
  const canDeploy = can("app.deploy");
  const canViewObservability = can("app.viewObservability");
  const canImport = can("import.manage");

  const [links, setLinks] = useState<ProjectLink[]>([]);
  useEffect(() => {
    if (!canViewObservability) return;
    getProjectLinks(projectName)
      .then(setLinks)
      .catch(() => setLinks([]));
  }, [projectName, canViewObservability]);

  const grafana = links.find((l) => l.id.startsWith("grafana") && !l.app);

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
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span>{projectName}</span>
            {health.data && liveApps.length > 0 && (
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
                  STATUS_TEXT[attention > 0 ? "degraded" : "healthy"],
                )}
              >
                <StatusDot status={attention > 0 ? "degraded" : "healthy"} />
                {attention > 0 ? th("attention", { count: attention }) : th("allHealthy")}
              </span>
            )}
          </span>
        }
        description={
          role
            ? th("description", {
                count: liveApps.length,
                cloud: targetCloud ? tNav(`cloud.${targetCloud}`) : t("kubernetes"),
                role: tRoles(`roles.${role}`),
              })
            : th("descriptionNoRole", {
                count: liveApps.length,
                cloud: targetCloud ? tNav(`cloud.${targetCloud}`) : t("kubernetes"),
              })
        }
        icon={FolderKanban}
        breadcrumbs={[
          { label: tNav("projects"), href: "/projects" },
          { label: projectName },
        ]}
        actions={
          <>
              {can("app.viewLogs") && (
                <Link
                  href={`/projects/${encodeURIComponent(projectName)}/logs`}
                  className={buttonVariants({ size: "sm", variant: "outline" })}
                >
                  <ScrollText aria-hidden />
                  {th("logs")}
                </Link>
              )}
              {grafana && (
                <a
                  href={grafana.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={buttonVariants({ size: "sm", variant: "outline" })}
                >
                  {th("grafana")}
                  <ExternalLink aria-hidden />
                </a>
              )}
              {canViewObservability && <ToolsMenu links={links} />}
              {canImport && (
                <Link
                  href={`/projects/${encodeURIComponent(projectName)}/import`}
                  className={buttonVariants({ size: "sm", variant: "outline" })}
                >
                  <Upload aria-hidden />
                  {t("importRepo")}
                </Link>
              )}
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
        <MoveBanner />
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

          {isNotConnected(appsError) ? (
            <NotConnected cloud={isNotConnected(appsError)!.cloud} />
          ) : appsError ? (
            <div className="rounded-xl border border-destructive bg-destructive/5 p-6 text-destructive space-y-2">
              <p className="font-medium">{t("appsLoadFailed")}</p>
              <p className="text-sm">{appsError}</p>
              <Button variant="outline" size="sm" onClick={refreshApps}>
                {tCommon("retry")}
              </Button>
            </div>
          ) : !loadingApps && apps.length === 0 ? (
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
            <AppsList
              apps={apps}
              health={health.data?.apps ?? null}
              loading={loadingApps || healthLoading}
              projectName={projectName}
              live={
                hasActiveDeployments ? (
                  <Badge
                    variant="outline"
                    className="animate-pulse border-primary/20 bg-primary/10 text-primary"
                  >
                    {t("live")}
                  </Badge>
                ) : null
              }
            />
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <GettingStarted />
            <ProjectClusterCard />
          </div>

          {can("topology.view") && (
            <ImportsList projectName={projectName} canManage={canImport} />
          )}

          <PlacementPanel />
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
