"use client";

import { useCan, useProjectApps } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { Deployment } from "@/types";
import { Boxes, ChevronDown, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  PROJECT_SECTIONS,
  projectHref,
  projectSegmentFromPath,
  type ProjectSectionEntry,
} from "./projectSections";

const MAX_LISTED_APPS = 8;
const APPS_REFRESH_MS = 8000;

const STATUS_DOT: Record<Deployment["status"], string> = {
  running: "bg-green-500",
  degraded: "bg-orange-500",
  failed: "bg-red-500",
  pending: "bg-amber-400",
  initializing: "bg-amber-400",
  planning: "bg-amber-400",
  deploying: "bg-amber-400",
  deleting: "bg-amber-400",
  deleted: "bg-muted-foreground",
};

const linkClass = (active: boolean, collapsed: boolean) =>
  cn(
    "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
    active
      ? "bg-primary/10 font-medium text-primary"
      : "text-foreground/80 hover:bg-accent hover:text-foreground",
    collapsed && "justify-center",
  );

export function ProjectNav({
  projectName,
  collapsed,
  onNavigate,
}: {
  projectName: string;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const t = useTranslations("projectNav");
  const pathname = usePathname();
  const { can } = useCan(projectName);
  const { apps, refresh } = useProjectApps(projectName);
  const [appsOpen, setAppsOpen] = useState(true);

  useEffect(() => {
    const timer = setInterval(refresh, APPS_REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const segment = projectSegmentFromPath(pathname);
  const onAppRoute = pathname.split("/")[3] === "apps";
  const currentAppId = onAppRoute ? pathname.split("/")[4] : null;
  const liveApps = apps.filter((a) => a.status !== "deleted");
  const shownApps = liveApps.slice(0, MAX_LISTED_APPS);

  const visible = (section: ProjectSectionEntry) =>
    !section.capability || can(section.capability);

  const renderSection = (section: ProjectSectionEntry) => {
    if (!visible(section)) return null;
    const Icon = section.icon;
    const label = t(section.key);
    const active = !onAppRoute && segment === section.segment;
    return (
      <Link
        key={section.key}
        href={projectHref(projectName, section.segment)}
        title={label}
        aria-current={active ? "page" : undefined}
        aria-label={collapsed ? label : undefined}
        onClick={onNavigate}
        className={linkClass(active, collapsed)}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        {!collapsed && <span className="flex-1 truncate">{label}</span>}
      </Link>
    );
  };

  const appsLabel = t("applications");

  return (
    <nav
      aria-label={t("navigation")}
      className="flex-1 space-y-0.5 overflow-y-auto px-2 py-3"
    >
      {renderSection(PROJECT_SECTIONS.overview)}

      {collapsed ? (
        <Link
          href={projectHref(projectName)}
          title={appsLabel}
          aria-label={appsLabel}
          onClick={onNavigate}
          className={linkClass(onAppRoute, true)}
        >
          <Boxes className="h-4 w-4 shrink-0" aria-hidden />
        </Link>
      ) : (
        <div>
          <button
            type="button"
            onClick={() => setAppsOpen((v) => !v)}
            aria-expanded={appsOpen}
            aria-label={appsOpen ? t("collapseApps") : t("expandApps")}
            className={cn(linkClass(false, false), "w-full text-left")}
          >
            <Boxes className="h-4 w-4 shrink-0" aria-hidden />
            <span className="flex-1 truncate">{appsLabel}</span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {liveApps.length}
            </span>
            {appsOpen ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
            )}
          </button>
          {appsOpen && (
            <ul className="ml-4 mt-0.5 space-y-0.5 border-l pl-2">
              {liveApps.length === 0 && (
                <li className="px-2 py-1 text-xs text-muted-foreground">
                  {t("noApps")}
                </li>
              )}
              {shownApps.map((app) => {
                const active = currentAppId === String(app.id);
                return (
                  <li key={app.id}>
                    <Link
                      href={`${projectHref(projectName)}/apps/${app.id}`}
                      title={app.name}
                      aria-current={active ? "page" : undefined}
                      onClick={onNavigate}
                      className={linkClass(active, false)}
                    >
                      <span
                        role="img"
                        aria-label={t(`status.${app.status}`)}
                        title={t(`status.${app.status}`)}
                        className={cn(
                          "h-2 w-2 shrink-0 rounded-full",
                          STATUS_DOT[app.status],
                        )}
                      />
                      <span className="flex-1 truncate">{app.name}</span>
                    </Link>
                  </li>
                );
              })}
              {liveApps.length > MAX_LISTED_APPS && (
                <li>
                  <Link
                    href={projectHref(projectName)}
                    onClick={onNavigate}
                    className="block rounded-md px-2 py-1 text-xs text-primary hover:underline"
                  >
                    {t("seeAll")}
                  </Link>
                </li>
              )}
            </ul>
          )}
        </div>
      )}

      {renderSection(PROJECT_SECTIONS.topology)}
      {renderSection(PROJECT_SECTIONS.members)}

      <div className="mx-2 my-2 border-t" role="separator" />

      {renderSection(PROJECT_SECTIONS.costs)}
      {renderSection(PROJECT_SECTIONS.security)}
      {renderSection(PROJECT_SECTIONS.activity)}
      {renderSection(PROJECT_SECTIONS.alerts)}
      {renderSection(PROJECT_SECTIONS.assistants)}

      <div className="mx-2 my-2 border-t" role="separator" />

      {renderSection(PROJECT_SECTIONS.settings)}
    </nav>
  );
}
