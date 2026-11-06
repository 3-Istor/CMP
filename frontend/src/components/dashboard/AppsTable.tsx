"use client";

import { AppIcon } from "@/components/app-icon/AppIcon";
import { HealthBadge } from "@/components/projects/DeploymentHealth";
import { Skeleton } from "@/components/ui/skeleton";
import { useAppHealth } from "@/lib/hooks";
import { appUrlOf, cn } from "@/lib/utils";
import type { Deployment } from "@/types";
import { ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

function appHref(app: Deployment) {
  return `/projects/${encodeURIComponent(app.project_id ?? "")}/apps/${app.id}`;
}

function AppState({ app }: { app: Deployment }) {
  const t = useTranslations("appPage.deploymentStatus");
  const live = app.provider_type === "kubernetes" && app.status === "running";
  const { health, loading } = useAppHealth(live ? app.id : null, 60_000);
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
  if (loading && apps.length === 0) {
    return (
      <div className="space-y-2 p-4">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }
  if (apps.length === 0) {
    return <p className="p-6 text-center text-sm text-muted-foreground">{t("empty")}</p>;
  }
  const shown = limit ? apps.slice(0, limit) : apps;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th className="px-4 py-2 font-medium">{t("name")}</th>
            {showProject && <th className="px-4 py-2 font-medium">{t("project")}</th>}
            <th className="px-4 py-2 font-medium">{t("state")}</th>
            <th className="hidden px-4 py-2 font-medium md:table-cell">{t("url")}</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((app) => {
            const url = appUrlOf(app.terraform_outputs);
            return (
              <tr key={app.id} className="border-b last:border-0 hover:bg-muted/40">
                <td className="px-4 py-2.5">
                  <Link href={appHref(app)} className="flex items-center gap-2.5 font-medium hover:underline">
                    <AppIcon
                      appId={app.id}
                      templateIcon={app.template_icon}
                      icon={app.icon}
                      className="h-7 w-7"
                    />
                    <span className="truncate">{app.name}</span>
                  </Link>
                </td>
                {showProject && (
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {app.project_id && (
                      <Link
                        href={`/projects/${encodeURIComponent(app.project_id)}`}
                        className="hover:text-foreground hover:underline"
                      >
                        {app.project_id}
                      </Link>
                    )}
                  </td>
                )}
                <td className="px-4 py-2.5">
                  <AppState app={app} />
                </td>
                <td className="hidden px-4 py-2.5 md:table-cell">
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                      aria-label={t("openApp", { name: app.name })}
                    >
                      {url.replace(/^https:\/\//, "")}
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </a>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
