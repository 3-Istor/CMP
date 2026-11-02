"use client";

import { AppIcon } from "@/components/app-icon/AppIcon";
import { AppIconDialog } from "@/components/app-icon/AppIconDialog";
import { FavoriteToggle } from "@/components/favorites/FavoriteToggle";

import { useAppContext } from "@/components/projects/AppContext";
import {
  NotAllowed,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { Badge } from "@/components/ui/badge";
import { badgeVariant, StateLabel } from "@/components/projects/DeploymentHealth";
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
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteDeployment,
  getDeploymentLinks,
  getPower,
  restartDeployment,
  sleepDeployment,
  wakeDeployment,
} from "@/lib/api";
import { useAppHealth } from "@/lib/hooks";
import { intlLocale } from "@/components/finops/format";
import { cn, appUrlOf } from "@/lib/utils";
import type { ProjectLink } from "@/types";
import {
  BellRing,
  ExternalLink,
  FileCode,
  Globe,
  History,
  Image as ImageIcon,
  Layers,
  MoreHorizontal,
  Moon,
  PackageCheck,
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
  const canViewFinops = can("finops.view");
  const canViewObservability = can("app.viewObservability");
  const canViewRelease = can("release.view");

  const isKubernetes = current.provider_type === "kubernetes";
  const isRunning = current.status === "running";
  const appUrl = isRunning ? appUrlOf(current.terraform_outputs) : null;

  const tf = useTranslations("appPage");
  const tFrame = useTranslations("apps.frame");
  const tApps = useTranslations("apps");
  const locale = intlLocale(useLocale());
  const [links, setLinks] = useState<ProjectLink[]>([]);
  const linksReady = isKubernetes && canViewLogs;
  useEffect(() => {
    if (!linksReady) return;
    getDeploymentLinks(appId)
      .then(setLinks)
      .catch(() => setLinks([]));
  }, [appId, linksReady, current.github_repo_url]);

  const inProgress = IN_PROGRESS_STATUSES.includes(current.status);
  const healthReady = isKubernetes && canViewLogs && !inProgress;
  const {
    health,
    loading: healthLoading,
  } = useAppHealth(healthReady ? appId : null, 15_000);
  const gone = current.status === "deleted";
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
      key: "deployments",
      segment: "deployments",
      icon: PackageCheck,
      visible: canViewLogs,
    },
    {
      key: "release",
      segment: "release",
      icon: Rocket,
      visible: isKubernetes && canViewRelease,
    },
    {
      key: "environments",
      segment: "environments",
      icon: Layers,
      visible: isKubernetes && canViewRelease,
    },
    {
      key: "config",
      segment: "config",
      icon: Settings,
      visible: isKubernetes && canViewConfig,
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
      key: "activity",
      segment: "activity",
      icon: History,
      visible: isKubernetes && canViewObservability,
    },
    {
      key: "alerts",
      segment: "alerts",
      icon: BellRing,
      visible: isKubernetes && canViewObservability,
    },
  ];

  return (
    <div className="min-h-screen">
      <main className="px-6 py-8 max-w-5xl mx-auto space-y-6">
        <nav aria-label={t("breadcrumb")} className="text-sm">
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
            <li aria-current="page" className="font-medium text-foreground">
              {current.name}
            </li>
          </ol>
        </nav>

        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <AppIcon
              appId={current.id}
              templateIcon={current.template_icon}
              icon={current.icon}
              className="h-9 w-9"
            />
            <div>
              <div className="flex items-center gap-2.5">
                <h1 className="text-2xl font-bold">{current.name}</h1>
                <FavoriteToggle kind="app" id={current.id} />
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
              </div>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                {current.created_via && (
                  <Badge
                    variant="outline"
                    className="border-primary/40 text-primary"
                    title={tFrame("aiCreated")}
                  >
                    {tFrame("via", { source: current.created_via })}
                  </Badge>
                )}
                <Badge variant="outline" className="capitalize text-xs">
                  {current.provider_type === "kubernetes"
                    ? "Kubernetes"
                    : tFrame("legacy")}
                </Badge>
                {current.k8s_namespace && (
                  <Badge variant="outline" className="font-mono text-xs">
                    {tApps("namespace", { namespace: current.k8s_namespace })}
                  </Badge>
                )}
              </div>
              {isKubernetes && canViewLogs && appUrl && (
                <a
                  href={appUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1.5 inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
                  aria-label={`${ta("openApp")}, ${ta("externalLink")}`}
                >
                  <Globe className="h-3.5 w-3.5" aria-hidden />
                  {appUrl.replace(/^https?:\/\//, "")}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
              <p className="text-xs text-muted-foreground mt-1.5">
                {current.template_name || current.template_id} ·{" "}
                {tFrame("deployedOn", {
                  date: new Date(current.created_at).toLocaleDateString(locale, {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                  }),
                })}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
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
                      <Link href={`${base}/config`}>
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
                        disabled={["deleting", "deleted"].includes(
                          current.status,
                        )}
                        onClick={() => setConfirming("delete")}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                        {ta("deleteApp")}
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {isKubernetes && canViewObservability && (
          <SecurityAlert
            project={projectName}
            app={current.name}
            onOpen={() => router.push(`${base}/security`)}
          />
        )}

        <Separator />

        <nav
          aria-label={t("label")}
          className="flex flex-wrap items-center gap-1 rounded-lg bg-muted p-[3px]"
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
                    "inline-flex items-center gap-2 rounded-md px-2.5 py-1 text-sm font-medium transition-colors",
                    active
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4" aria-hidden />
                  {t(tab.key)}
                </Link>
              );
            })}
        </nav>

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

function MenuText({ label, reason }: { label: string; reason: string | null }) {
  return (
    <span className="flex flex-col">
      <span>{label}</span>
      {reason && (
        <span className="text-xs font-normal text-muted-foreground">
          {reason}
        </span>
      )}
    </span>
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
