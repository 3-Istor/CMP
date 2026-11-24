"use client";

import { FinishImportBanner } from "@/components/imports/ImportTasks";
import { AiAccessBadge } from "@/components/ai/AiAccessBadge";
import { AppIcon } from "@/components/app-icon/AppIcon";
import { Breadcrumbs } from "@/components/layout/PageHeader";
import { projectHref } from "@/components/layout/projectSections";
import { AppIconDialog } from "@/components/app-icon/AppIconDialog";
import { FavoriteToggle } from "@/components/favorites/FavoriteToggle";

import { useAppContext } from "@/components/projects/AppContext";
import {
  NotAllowed,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { useObsCapabilities } from "@/components/observability/shared";
import { useRollback } from "@/components/app-overview/Rollback";
import { STATUS_TEXT, useReasonLabel } from "@/components/app-overview/shared";
import { liveDeploy } from "@/lib/appHealth";
import { useAppSummary } from "@/lib/appSummary";
import { ScoreBadge } from "@/components/scorecard/ScoreBadge";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { Badge } from "@/components/ui/badge";
import { HealthBadge } from "@/components/projects/DeploymentHealth";
import { MenuText } from "@/components/projects/MenuText";
import { EnvLinks } from "@/components/projects/EnvLinks";
import { ToolsMenu } from "@/components/projects/ToolsMenu";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DeletingBadge,
  ProgressBadge,
} from "@/components/projects/DeletingBadge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteDeployment,
  getDeletionProtection,
  getDeploymentLinks,
  getPower,
  pauseDeployment,
  requestSecurityBackup,
  resumeDeployment,
  restartDeployment,
  sleepDeployment,
  wakeDeployment,
} from "@/lib/api";
import { useAppHealth } from "@/lib/hooks";
import { hasScorecard, useScorecards } from "@/lib/scorecard";
import { intlLocale } from "@/components/finops/format";
import { cn, appUrlOf } from "@/lib/utils";
import type { Deployment, ProjectLink } from "@/types";
import {
  Check,
  DatabaseBackup,
  ExternalLink,
  FileCode,
  History,
  Image as ImageIcon,
  MoreHorizontal,
  Moon,
  Pause,
  Play,
  RefreshCw,
  Rocket,
  ScrollText,
  RotateCw,
  Settings,
  Shield,
  Sun,
  Trash2,
  Wallet,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const IN_PROGRESS_STATUSES = [
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
];

interface TabEntry {
  key: string;
  segment: string;
  icon: LucideIcon;
  visible: boolean;
}

export function AppFrame({ children }: { children: React.ReactNode }) {
  const t = useTranslations("apps.nav");
  const router = useRouter();
  const pathname = usePathname();
  const { appId, projectName, deployment: current, refresh, markDeleting } =
    useAppContext();
  const { can } = useProjectContext();

  const ta = useTranslations("actions");
  const tc = useTranslations("confirm");
  const [confirming, setConfirming] = useState<
    "sleep" | "pause" | "resume" | "restart" | "delete" | null
  >(null);

  const canDelete = can("app.delete");
  const canEditIcon = can("app.editIcon");
  const canPause = can("app.pause");
  const [iconOpen, setIconOpen] = useState(false);
  const tIcon = useTranslations("appIcon");
  const canOperate = can("app.operate");
  const canViewLogs = can("app.viewLogs");
  const canViewConfig = can("app.viewConfig");
  const canViewRelease = can("release.view");
  const canViewFinops = can("finops.view");
  const canViewObservability = can("app.viewObservability");

  const isKubernetes = current.provider_type === "kubernetes";
  const imported = current.template_id.startsWith("import-");
  const obsCaps = useObsCapabilities(
    appId,
    isKubernetes && canViewObservability,
  ).data;

  const tf = useTranslations("appPage");
  const tNavProj = useTranslations("projectNav");
  const tFrame = useTranslations("apps.frame");
  const tOv = useTranslations("appOverview");
  const reasonLabel = useReasonLabel();
  const locale = intlLocale(useLocale());
  const [links, setLinks] = useState<ProjectLink[]>([]);
  const linksReady = isKubernetes && canViewLogs;
  useEffect(() => {
    if (!linksReady) return;
    getDeploymentLinks(appId)
      .then(setLinks)
      .catch(() => setLinks([]));
  }, [appId, linksReady, current.github_repo_url]);

  const [protectedOn, setProtectedOn] = useState(false);
  useEffect(() => {
    if (!canDelete) return;
    getDeletionProtection(projectName)
      .then(({ enabled }) => setProtectedOn(enabled))
      .catch(() => setProtectedOn(false));
  }, [projectName, canDelete]);

  const inProgress = IN_PROGRESS_STATUSES.includes(current.status);
  const healthReady = isKubernetes && canViewLogs && !inProgress;
  const {
    health,
    loading: healthLoading,
  } = useAppHealth(healthReady ? appId : null, 15_000);
  const gone = current.status === "deleted";
  const outputsUrl = appUrlOf(current.terraform_outputs);
  // URL and "Deployed" once the run finished and the app is healthy (or asleep
  // on its schedule); legacy apps have no live health.
  const live =
    current.status === "running" &&
    (!isKubernetes ||
      health?.status === "healthy" ||
      health?.status === "asleep");
  const appUrl = live ? outputsUrl : null;
  const urlSoon =
    Boolean(outputsUrl) &&
    !live &&
    !healthLoading &&
    (current.status === "deploying" || current.status === "running");
  const stage = stageOf(current);
  // Healthy is the common case: no "Created on" flash before the first answer.
  const deployedLabel =
    live || (current.status === "running" && healthLoading);
  const scoreCard = useScorecards(
    [current.id],
    canViewLogs && hasScorecard(current),
  )[current.id];
  const summary = useAppSummary(appId, "24h", healthReady).data;
  const rollback = useRollback(
    appId,
    summary?.deploys ?? [],
    healthReady && can("release.operate"),
  );
  const live24 = summary ? liveDeploy(summary.deploys) : null;
  const statusReason =
    summary && summary.status !== "healthy" && summary.status_reason !== "asleep"
      ? reasonLabel(summary.status_reason)
      : null;
  const grafanaUrl =
    obsCaps?.grafana_url ||
    links.find((l) => l.id.startsWith("grafana") && l.app)?.url ||
    null;
  const canBackUp =
    summary?.kind === "datastore" && can("security.manage");
  const handleBackup = async () => {
    try {
      const { backups } = await requestSecurityBackup(projectName, current.name);
      toast.success(tOv("header.backupStarted", { list: backups.join(", ") }));
    } catch (err) {
      toast.error(String(err));
    }
  };
  const powerReadable =
    canOperate &&
    isKubernetes &&
    (current.status === "running" || current.status === "degraded");

  const [power, setPower] = useState<{
    asleep: boolean;
    scheduled: boolean;
    paused: boolean;
  } | null>(null);
  const [powerFailed, setPowerFailed] = useState(false);
  const loadPower = useCallback(() => {
    getPower(appId)
      .then((state) => {
        setPower(state);
        setPowerFailed(false);
      })
      .catch(() => setPowerFailed(true));
  }, [appId]);
  useEffect(() => {
    if (powerReadable) loadPower();
  }, [powerReadable, loadPower, current.status]);

  const powerReason = (): string | null => {
    if (inProgress || gone) return ta("reasons.busy");
    if (powerFailed) return ta("reasons.unavailable");
    if (!power) return ta("reasons.loading");
    if (!power.scheduled) return ta("reasons.noSchedule");
    return null;
  };
  const restartReason = (): string | null => {
    if (inProgress || gone) return ta("reasons.busy");
    if (powerFailed) return ta("reasons.unavailable");
    if (!power) return ta("reasons.loading");
    if (power.asleep) return ta("reasons.asleep");
    return null;
  };

  const handleWake = async () => {
    try {
      await wakeDeployment(appId);
      toast.success(ta("toasts.woken", { name: current.name }));
      loadPower();
    } catch (err) {
      toast.error(ta("errors.wake", { message: String(err) }));
    }
  };

  const handleSleep = async () => {
    await sleepDeployment(appId);
    toast.success(ta("toasts.sleeping", { name: current.name }));
    loadPower();
  };

  const handlePause = async () => {
    await pauseDeployment(appId);
    toast.success(ta("toasts.paused", { name: current.name }));
    loadPower();
  };

  const handleResume = async () => {
    await resumeDeployment(appId);
    toast.success(ta("toasts.resumed", { name: current.name }));
    loadPower();
  };

  const handleRestart = async () => {
    await restartDeployment(appId);
    toast.success(ta("toasts.restarting", { name: current.name }));
  };

  const handleDelete = async () => {
    if (!canDelete) throw new Error(ta("errors.notAllowed"));
    await deleteDeployment(appId);
    toast.success(ta("toasts.deleting", { name: current.name }));
    markDeleting();
  };

  const handleRefresh = () => {
    refresh();
    if (powerReadable) loadPower();
    toast.success(ta("refreshed"));
  };

  const base = `/projects/${encodeURIComponent(projectName)}/apps/${appId}`;
  const activeSegment = pathname.split("/")[5] ?? "";

  const tabs: TabEntry[] = [
    { key: "app", segment: "", icon: FileCode, visible: true },
    {
      key: "logs",
      segment: "logs",
      icon: ScrollText,
      visible: isKubernetes && canViewLogs,
    },
    {
      key: "traces",
      segment: "traces",
      icon: Waypoints,
      visible: isKubernetes && canViewObservability && Boolean(obsCaps?.traces),
    },
    {
      key: "delivery",
      segment: "delivery",
      icon: Rocket,
      visible: canViewLogs || (isKubernetes && canViewRelease),
    },
    {
      key: "costs",
      segment: "costs",
      icon: Wallet,
      visible: canViewFinops,
    },
    {
      key: "security",
      segment: "security",
      icon: Shield,
      visible: isKubernetes && canViewObservability,
    },
    {
      key: "settings",
      segment: "settings",
      icon: Settings,
      visible: isKubernetes && canViewConfig,
    },
  ];
  const activeTab = tabs.find((tab) => tab.segment === activeSegment);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto max-w-7xl px-6 pt-4">
          <Breadcrumbs
            items={[
              { label: tNavProj("projects"), href: "/projects" },
              { label: projectName, href: projectHref(projectName) },
              { label: current.name, href: activeSegment ? base : undefined },
              ...(activeSegment && activeTab
                ? [{ label: t(activeTab.key) }]
                : []),
            ]}
          />

          <div className="flex items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <AppIcon
                appId={current.id}
                templateIcon={current.template_icon}
                icon={current.icon}
                className="h-10 w-10 shrink-0"
              />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="truncate text-xl font-semibold tracking-tight">
                    {current.name}
                  </h1>
                  {healthReady && (
                    <HealthPill
                      loading={healthLoading}
                      status={health?.status ?? null}
                    />
                  )}
                  {statusReason && (
                    <Badge
                      variant="outline"
                      className={cn("px-1.5 py-0 text-[11px]", STATUS_TEXT[summary!.status])}
                    >
                      {statusReason}
                    </Badge>
                  )}
                  {imported && (
                    <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                      {tOv("badges.imported")}
                    </Badge>
                  )}
                  {summary && summary.kind !== "web" && (
                    <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                      {tOv(`badges.${summary.kind}`)}
                    </Badge>
                  )}
                  {summary && summary.kind !== "web" && !outputsUrl && (
                    <Badge variant="outline" className="px-1.5 py-0 text-[11px] text-muted-foreground">
                      {tOv("badges.internal")}
                    </Badge>
                  )}
                  {current.status === "deleting" && <DeletingBadge />}
                  {stage !== null && <DeployStages stage={stage} />}
                  {current.status === "failed" && (
                    <span className="text-xs text-destructive">
                      {tf("deploymentStatus.failed")}
                    </span>
                  )}
                  <ScoreBadge card={scoreCard} />
                  <AiAccessBadge project={projectName} app={current.name} />
                  <FavoriteToggle kind="app" id={current.id} />
                </div>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                  {isKubernetes && canViewLogs && (
                    <EnvLinks prod={appUrl} staging={live ? current.staging_url : null} />
                  )}
                  {isKubernetes && canViewLogs && urlSoon && (
                    <span>{tFrame("urlSoon")}</span>
                  )}
                  {live24 ? (
                    <span>
                      {tOv("header.liveSince", {
                        version: live24.version,
                        date: new Date(live24.at).toLocaleDateString(locale, {
                          day: "numeric",
                          month: "short",
                        }),
                      })}
                    </span>
                  ) : (
                    <span>
                      {tFrame(deployedLabel ? "deployedOn" : "createdOn", {
                        date: new Date(current.created_at).toLocaleDateString(
                          locale,
                          { day: "numeric", month: "short", year: "numeric" },
                        ),
                      })}
                    </span>
                  )}
                  <span title={current.k8s_namespace ?? undefined}>
                    {current.template_name || current.template_id}
                  </span>
                  {current.created_via && (
                    <Badge
                      variant="outline"
                      className="border-primary/40 px-1.5 py-0 text-[11px] text-primary"
                      title={tFrame("aiCreated")}
                    >
                      {tFrame("via", { source: current.created_via })}
                    </Badge>
                  )}
                  {!isKubernetes && (
                    <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                      {tFrame("legacy")}
                    </Badge>
                  )}
                </p>
              </div>
            </div>

            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
              {isKubernetes && canViewLogs && (
                <Link
                  href={`${base}/logs`}
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  <ScrollText aria-hidden />
                  {tOv("header.logs")}
                </Link>
              )}
              {canOperate && isKubernetes && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={restartReason() !== null}
                  title={restartReason() ?? undefined}
                  onClick={() => setConfirming("restart")}
                >
                  <RotateCw aria-hidden />
                  {tOv("header.restart")}
                </Button>
              )}
              {rollback.target && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => rollback.ask(rollback.target)}
                >
                  <History aria-hidden />
                  {tOv("header.rollbackTo", { version: rollback.target.version })}
                </Button>
              )}
              {canBackUp && (
                <Button variant="outline" size="sm" onClick={handleBackup}>
                  <DatabaseBackup aria-hidden />
                  {tOv("header.backupNow")}
                </Button>
              )}
              {grafanaUrl && canViewObservability && (
                <a
                  href={grafanaUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  {tOv("header.grafana")}
                  <ExternalLink aria-hidden />
                </a>
              )}
              {appUrl && (
                <a
                  href={appUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={buttonVariants({ size: "sm" })}
                >
                  {tOv("header.openApp")}
                  <ExternalLink aria-hidden />
                </a>
              )}
              {linksReady && <ToolsMenu links={links} />}
              <Button
                variant="ghost"
                size="icon"
                onClick={handleRefresh}
                title={ta("refresh")}
                aria-label={ta("refresh")}
              >
                <RefreshCw aria-hidden />
              </Button>
              {(canOperate || canViewConfig || canDelete || canEditIcon) && (
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
                  <DropdownMenuContent align="end" className="min-w-[15rem]">
                    {canOperate && isKubernetes && (
                      <>
                        {canPause && (
                          <DropdownMenuItem
                            className="gap-2"
                            disabled={powerReason() !== null}
                            onClick={() =>
                              setConfirming(power?.paused ? "resume" : "pause")
                            }
                          >
                            {power?.paused ? (
                              <Play className="h-4 w-4" aria-hidden />
                            ) : (
                              <Pause className="h-4 w-4" aria-hidden />
                            )}
                            <MenuText
                              label={ta(power?.paused ? "resume" : "pause")}
                              reason={powerReason()}
                            />
                          </DropdownMenuItem>
                        )}
                        {power?.paused ? null : power?.asleep ? (
                          <DropdownMenuItem
                            className="gap-2"
                            disabled={powerReason() !== null}
                            onClick={handleWake}
                          >
                            <Sun className="h-4 w-4" aria-hidden />
                            <MenuText
                              label={ta("wake")}
                              reason={powerReason()}
                            />
                          </DropdownMenuItem>
                        ) : (
                          <DropdownMenuItem
                            className="gap-2"
                            disabled={powerReason() !== null}
                            onClick={() => setConfirming("sleep")}
                          >
                            <Moon className="h-4 w-4" aria-hidden />
                            <MenuText
                              label={ta("sleep")}
                              reason={powerReason()}
                            />
                          </DropdownMenuItem>
                        )}
                      </>
                    )}
                    {canEditIcon && (
                      <DropdownMenuItem
                        className="gap-2"
                        onClick={() => setIconOpen(true)}
                      >
                        <ImageIcon className="h-4 w-4" aria-hidden />
                        {tIcon("menu")}
                      </DropdownMenuItem>
                    )}
                    {isKubernetes && canViewConfig && (
                      <DropdownMenuItem asChild className="gap-2">
                        <Link href={`${base}/settings`}>
                          <Settings className="h-4 w-4" aria-hidden />
                          {ta("settings")}
                        </Link>
                      </DropdownMenuItem>
                    )}
                    {canDelete && (
                      <>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="gap-2 text-destructive hover:text-destructive focus:text-destructive"
                          disabled={
                            protectedOn ||
                            imported ||
                            ["deleting", "deleted"].includes(current.status)
                          }
                          onClick={() => setConfirming("delete")}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                          <MenuText
                            label={ta("deleteApp")}
                            reason={
                              protectedOn
                                ? ta("reasons.protected")
                                : imported
                                  ? ta("reasons.imported")
                                  : null
                            }
                          />
                        </DropdownMenuItem>
                      </>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          </div>

          <nav
            aria-label={t("label")}
            className="-mb-px mt-3 flex gap-1 overflow-x-auto [scrollbar-width:none]"
          >
            {tabs
              .filter((tab) => tab.visible)
              .map((tab) => {
                const active = activeSegment === tab.segment;
                const Icon = tab.icon;
                return (
                  <Link
                    key={tab.key}
                    href={tab.segment ? `${base}/${tab.segment}` : base}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                      active
                        ? "border-primary text-foreground"
                        : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                    {t(tab.key)}
                  </Link>
                );
              })}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
        {isKubernetes && canViewObservability && activeSegment !== "security" && (
          <SecurityAlert
            project={projectName}
            app={current.name}
            onOpen={() => router.push(`${base}/security`)}
          />
        )}

        {isKubernetes && <FinishImportBanner />}

        <div className="space-y-6">{children}</div>
      </main>

      {rollback.dialog}
      <AppIconDialog
        deployment={current}
        open={iconOpen}
        onOpenChange={setIconOpen}
        onSaved={() => void refresh()}
      />
      <ConfirmDialog
        open={confirming === "sleep"}
        onOpenChange={(open) => !open && setConfirming(null)}
        level={1}
        title={tc("sleep.title", { name: current.name })}
        description={tc("sleep.description")}
        confirmLabel={tc("sleep.confirm")}
        onConfirm={handleSleep}
      />
      <ConfirmDialog
        open={confirming === "pause"}
        onOpenChange={(open) => !open && setConfirming(null)}
        level={1}
        title={tc("pause.title", { name: current.name })}
        description={tc("pause.description")}
        confirmLabel={tc("pause.confirm")}
        onConfirm={handlePause}
      />
      <ConfirmDialog
        open={confirming === "resume"}
        onOpenChange={(open) => !open && setConfirming(null)}
        level={1}
        title={tc("resume.title", { name: current.name })}
        description={tc("resume.description")}
        confirmLabel={tc("resume.confirm")}
        onConfirm={handleResume}
      />
      <ConfirmDialog
        open={confirming === "restart"}
        onOpenChange={(open) => !open && setConfirming(null)}
        level={1}
        title={tc("restart.title", { name: current.name })}
        description={tc("restart.description")}
        confirmLabel={tc("restart.confirm")}
        onConfirm={handleRestart}
      />
      <ConfirmDialog
        open={confirming === "delete"}
        onOpenChange={(open) => !open && setConfirming(null)}
        level={2}
        destructive
        title={tc("deleteApp.title", { name: current.name })}
        description={tc("deleteApp.description")}
        consequences={[
          tc("deleteApp.consequences.resources"),
          tc("deleteApp.consequences.data"),
          tc("deleteApp.consequences.users"),
        ]}
        expectedName={current.name}
        confirmLabel={tc("deleteApp.confirm")}
        onConfirm={handleDelete}
      />
    </div>
  );
}

const STAGES = ["creating", "deploying", "waitingHealth", "live"] as const;

function stageOf(deployment: Deployment): number | null {
  if (["pending", "initializing", "planning"].includes(deployment.status)) {
    return 0;
  }
  // Outputs are written after the apply, before the wait for the app URL.
  if (deployment.status === "deploying") {
    return deployment.terraform_outputs ? 2 : 1;
  }
  return null;
}

function DeployStages({ stage }: { stage: number }) {
  const t = useTranslations("apps.frame.stages");
  return (
    <>
      <ProgressBadge label={t(STAGES[stage])} />
      {/* Narrow screens keep the pill only: the strip would wrap per stage. */}
      <span className="hidden text-xs text-muted-foreground sm:inline">
        {STAGES.map((key, i) => (
          <Fragment key={key}>
            {i > 0 && " · "}
            <span className={cn(i === stage && "font-medium text-foreground")}>
              {t(key)}
              {i < stage && <Check aria-label="done" className="ml-0.5 inline size-3 align-[-1px]" />}
            </span>
          </Fragment>
        ))}
      </span>
    </>
  );
}

function HealthPill({
  loading,
  status,
}: {
  loading: boolean;
  status: string | null;
}) {
  if (loading) return <Skeleton className="h-6 w-20 rounded-full" />;
  const state = status ?? "unknown";
  return (
    <HealthBadge state={state} role="status" />
  );
}

export function AppSection({
  allowed,
  children,
}: {
  allowed: boolean;
  children: React.ReactNode;
}) {
  const { roleLoading } = useProjectContext();
  if (allowed) return <>{children}</>;
  if (roleLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  return <NotAllowed />;
}
