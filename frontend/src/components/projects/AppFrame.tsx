"use client";

import { AppIcon } from "@/components/app-icon/AppIcon";
import { AppIconDialog } from "@/components/app-icon/AppIconDialog";
import { FavoriteToggle } from "@/components/favorites/FavoriteToggle";

import { useAppContext } from "@/components/projects/AppContext";
import {
  NotAllowed,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { ScoreBadge } from "@/components/scorecard/ScoreBadge";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { Badge } from "@/components/ui/badge";
import { badgeVariant, StateLabel } from "@/components/projects/DeploymentHealth";
import { MenuText } from "@/components/projects/MenuText";
import { ToolsMenu } from "@/components/projects/ToolsMenu";
import { Button } from "@/components/ui/button";
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
  restartDeployment,
  sleepDeployment,
  wakeDeployment,
} from "@/lib/api";
import { useAppHealth } from "@/lib/hooks";
import { hasScorecard, useScorecards } from "@/lib/scorecard";
import { intlLocale } from "@/components/finops/format";
import { cn, appUrlOf } from "@/lib/utils";
import type { ProjectLink } from "@/types";
import {
  ExternalLink,
  FileCode,
  Globe,
  Image as ImageIcon,
  MoreHorizontal,
  Moon,
  RefreshCw,
  Rocket,
  ScrollText,
  RotateCw,
  Settings,
  Shield,
  Sun,
  Trash2,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
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
    "sleep" | "restart" | "delete" | null
  >(null);

  const canDelete = can("app.delete");
  const canEditIcon = can("app.editIcon");
  const [iconOpen, setIconOpen] = useState(false);
  const tIcon = useTranslations("appIcon");
  const canOperate = can("app.operate");
  const canViewLogs = can("app.viewLogs");
  const canViewConfig = can("app.viewConfig");
  const canViewRelease = can("release.view");
  const canViewFinops = can("finops.view");
  const canViewObservability = can("app.viewObservability");

  const isKubernetes = current.provider_type === "kubernetes";
  const isRunning = current.status === "running";
  const appUrl = isRunning ? appUrlOf(current.terraform_outputs) : null;

  const tf = useTranslations("appPage");
  const tFrame = useTranslations("apps.frame");
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
  const scoreCard = useScorecards(
    [current.id],
    canViewLogs && hasScorecard(current),
  )[current.id];
  const powerReadable =
    canOperate &&
    isKubernetes &&
    (current.status === "running" || current.status === "degraded");

  const [power, setPower] = useState<{
    asleep: boolean;
    scheduled: boolean;
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

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto max-w-7xl px-6 pt-4">
          <nav aria-label={t("breadcrumb")} className="text-xs">
            <ol className="flex items-center gap-1.5 text-muted-foreground">
              <li>
                <Link
                  href={`/projects/${encodeURIComponent(projectName)}`}
                  className="hover:text-foreground hover:underline"
                >
                  {t("applications")}
                </Link>
              </li>
              <li aria-hidden>/</li>
              <li aria-current="page" className="text-foreground">
                {current.name}
              </li>
            </ol>
          </nav>

          <div className="mt-2 flex items-center justify-between gap-4">
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
                  {(inProgress || current.status === "failed") && (
                    <span
                      className={cn(
                        "text-xs text-muted-foreground",
                        current.status === "failed" && "text-destructive",
                      )}
                    >
                      {tf(`deploymentStatus.${current.status}`)}
                    </span>
                  )}
                  <ScoreBadge card={scoreCard} />
                  <FavoriteToggle kind="app" id={current.id} />
                </div>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                  {isKubernetes && canViewLogs && appUrl && (
                    <a
                      href={appUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                      aria-label={`${ta("openApp")}, ${ta("externalLink")}`}
                    >
                      <Globe className="h-3 w-3" aria-hidden />
                      {appUrl.replace(/^https?:\/\//, "")}
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </a>
                  )}
                  <span>{current.template_name || current.template_id}</span>
                  {current.k8s_namespace && (
                    <span className="font-mono">{current.k8s_namespace}</span>
                  )}
                  <span>
                    {tFrame("deployedOn", {
                      date: new Date(current.created_at).toLocaleDateString(
                        locale,
                        { day: "numeric", month: "short", year: "numeric" },
                      ),
                    })}
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

            <div className="flex shrink-0 items-center gap-2">
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
                        {power?.asleep ? (
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
                        <DropdownMenuItem
                          className="gap-2"
                          disabled={restartReason() !== null}
                          onClick={() => setConfirming("restart")}
                        >
                          <RotateCw className="h-4 w-4" aria-hidden />
                          <MenuText
                            label={ta("restart")}
                            reason={restartReason()}
                          />
                        </DropdownMenuItem>
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
                            ["deleting", "deleted"].includes(current.status)
                          }
                          onClick={() => setConfirming("delete")}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                          <MenuText
                            label={ta("deleteApp")}
                            reason={protectedOn ? ta("reasons.protected") : null}
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

        <div className="space-y-6">{children}</div>
      </main>

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
    <Badge variant={badgeVariant(state)} className="gap-1" role="status">
      <StateLabel state={state} />
    </Badge>
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
