"use client";

import { AppIcon } from "@/components/app-icon/AppIcon";
import { EnvLinks } from "@/components/projects/EnvLinks";
import { HealthBadge } from "@/components/projects/DeploymentHealth";
import { DeletingBadge } from "@/components/projects/DeletingBadge";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Skeleton } from "@/components/ui/skeleton";
import { useAppsHealth } from "@/lib/hooks";
import { appUrlOf, cn } from "@/lib/utils";
import type { AppHealthResponse, Deployment } from "@/types";
import { useTranslations } from "next-intl";
import Link from "next/link";

function appHref(app: Deployment) {
  return `/projects/${encodeURIComponent(app.project_id ?? "")}/apps/${app.id}`;
}

const isLive = (app: Deployment) =>
  app.provider_type === "kubernetes" && app.status === "running";

function AppState({
  app,
  health,
  loading,
}: {
  app: Deployment;
  health?: AppHealthResponse;
  loading: boolean;
}) {
  const t = useTranslations("appPage.deploymentStatus");
  const live = isLive(app);
  if (app.status === "deleting") return <DeletingBadge />;
  if (!live) {
    return (
      <span
        className={cn(
          "text-xs",
          app.status === "failed" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {t.has(app.status) ? t(app.status) : app.status}
      </span>
    );
  }
  if (loading && !health) return <Skeleton className="h-5 w-16 rounded-full" />;
  const state = health?.status ?? "unknown";
  return (
    <HealthBadge state={state} />
  );
}

export function AppsTable({
  apps,
  loading,
  limit,
  showProject = true,
}: {
  apps: Deployment[];
  loading: boolean;
  limit?: number;
  showProject?: boolean;
}) {
  const t = useTranslations("overview.apps");
  const shown = limit ? apps.slice(0, limit) : apps;
  // 60_000 = the per-row poll interval before the batch call.
  const { health, loading: healthLoading } = useAppsHealth(
    shown.filter(isLive).map((a) => a.id),
    60_000,
  );
  const columns: DataColumn<Deployment>[] = [
    {
      key: "name",
      header: t("name"),
      sortable: true,
      cell: (app) => (
        <Link href={appHref(app)} className="flex items-center gap-2.5 font-medium hover:underline">
          <AppIcon
            appId={app.id}
            templateIcon={app.template_icon}
            icon={app.icon}
            className="h-7 w-7"
          />
          <span className="truncate">{app.name}</span>
        </Link>
      ),
    },
    ...(showProject
      ? [
          {
            key: "project_id",
            header: t("project"),
            sortable: true,
            className: "text-muted-foreground",
            cell: (app: Deployment) =>
              app.project_id && (
                <Link
                  href={`/projects/${encodeURIComponent(app.project_id)}`}
                  className="hover:text-foreground hover:underline"
                >
                  {app.project_id}
                </Link>
              ),
          },
        ]
      : []),
    {
      key: "status",
      header: t("state"),
      sortable: true,
      cell: (app) => (
        <AppState app={app} health={health[app.id]} loading={healthLoading} />
      ),
    },
    {
      key: "url",
      header: t("url"),
      className: "hidden md:table-cell",
      cell: (app) => (
        <span className="flex items-center gap-3 text-xs">
          <EnvLinks prod={appUrlOf(app.terraform_outputs)} staging={app.staging_url} />
        </span>
      ),
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={shown}
      rowKey={(app) => app.id}
      loading={loading && apps.length === 0}
      storageKey={limit ? undefined : "apps.list"}
      {...(limit ? { pageSizeOptions: [limit], defaultPageSize: limit } : {})}
      empty={{ title: t("empty") }}
    />
  );
}
