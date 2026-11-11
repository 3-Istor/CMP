"use client";

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useFavorites } from "@/lib/favorites";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { usePlatformRoles } from "@/lib/usePlatformRoles";
import type { Deployment, Project } from "@/types";
import { Boxes, ExternalLink, FolderKanban, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useMemo } from "react";
import { readRecentApps } from "./recentApps";
import { readRecentProjects } from "./recentProjects";
import {
  ADMIN_LINKS,
  ALL_PAGES,
  PUBLIC_LINKS,
  isPageVisible,
  useVisibleWork,
} from "./shellData";

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("shell.palette");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        onOpenChange(!open);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title={t("title")}>
      <PaletteBody close={() => onOpenChange(false)} />
    </CommandDialog>
  );
}

const MAX_RECENT_SHOWN = 5;

function PaletteBody({ close }: { close: () => void }) {
  const t = useTranslations("shell.palette");
  const tNav = useTranslations("shell.sidebar");
  const tLinks = useTranslations("shell.links");
  const router = useRouter();
  const isAdmin = usePlatformAdmin();
  const platformRoles = usePlatformRoles();
  const { projects: allProjects, apps: allApps } = useVisibleWork(0);
  const tFav = useTranslations("favorites");
  const favorites = useFavorites();

  const { favProjects, favApps, recentProjects, recentApps, projects, apps } =
    useMemo(() => {
      const byRecency = <T,>(items: T[], recent: unknown[], key: (i: T) => unknown) =>
        recent.flatMap((r) => items.filter((i) => key(i) === r));
      const projectKey = (p: Project) => p.name;
      const appKey = (a: Deployment) => a.id;

      const favProjects = allProjects.filter((p) =>
        favorites.projects.includes(p.name),
      );
      const favApps = allApps.filter((a) => favorites.apps.includes(a.id));
      const restProjects = allProjects.filter((p) => !favProjects.includes(p));
      const restApps = allApps.filter((a) => !favApps.includes(a));
      const recentProjects = byRecency(
        restProjects,
        readRecentProjects(),
        projectKey,
      ).slice(0, MAX_RECENT_SHOWN);
      const recentApps = byRecency(restApps, readRecentApps(), appKey).slice(
        0,
        MAX_RECENT_SHOWN,
      );
      return {
        favProjects,
        favApps,
        recentProjects,
        recentApps,
        projects: restProjects.filter((p) => !recentProjects.includes(p)),
        apps: restApps.filter((a) => !recentApps.includes(a)),
      };
    }, [allProjects, allApps, favorites.projects, favorites.apps]);

  const projectItem = (project: Project, favorite = false) => (
    <CommandItem
      key={project.name}
      value={`project ${project.name}`}
      onSelect={() => go(`/projects/${project.name}`)}
    >
      {favorite ? <Star aria-hidden /> : <FolderKanban aria-hidden />}
      <span className="capitalize">{project.name}</span>
    </CommandItem>
  );

  const appItem = (app: Deployment) => (
    <CommandItem
      key={app.id}
      value={`app ${app.name} ${app.project_id} ${app.id}`}
      onSelect={() => go(`/projects/${app.project_id}/apps/${app.id}`)}
    >
      {app.template_icon ? (
        <span className="w-4 text-center text-base leading-none">
          {app.template_icon}
        </span>
      ) : (
        <Boxes aria-hidden />
      )}
      <span className="truncate">{app.name}</span>
      <span className="ml-auto truncate text-xs text-muted-foreground">
        {app.project_id}
      </span>
    </CommandItem>
  );

  const go = (href: string) => {
    close();
    router.push(href);
  };

  const openExternal = (href: string) => {
    close();
    window.open(href, "_blank", "noopener,noreferrer");
  };

  const links = isAdmin ? [...PUBLIC_LINKS, ...ADMIN_LINKS] : PUBLIC_LINKS;

  return (
    <Command>
      <CommandInput placeholder={t("placeholder")} />
      <CommandList>
        <CommandEmpty>{t("empty")}</CommandEmpty>

        {favProjects.length + favApps.length > 0 && (
          <CommandGroup heading={tFav("title")}>
            {favProjects.map((project) => projectItem(project, true))}
            {favApps.map(appItem)}
          </CommandGroup>
        )}

        {recentProjects.length + recentApps.length > 0 && (
          <CommandGroup heading={tFav("recents")}>
            {recentProjects.map((project) => projectItem(project))}
            {recentApps.map(appItem)}
          </CommandGroup>
        )}

        <CommandGroup heading={t("pages")}>
          {ALL_PAGES.filter((page) =>
            isPageVisible(page, platformRoles),
          ).map(({ key, href, icon: Icon }) => (
            <CommandItem
              key={key}
              value={`page ${tNav(key)}`}
              onSelect={() => go(href)}
            >
              <Icon aria-hidden />
              {tNav(key)}
            </CommandItem>
          ))}
        </CommandGroup>

        {projects.length > 0 && (
          <CommandGroup heading={t("projects")}>
            {projects.map((project) => projectItem(project))}
          </CommandGroup>
        )}

        {apps.length > 0 && (
          <CommandGroup heading={t("apps")}>{apps.map(appItem)}</CommandGroup>
        )}

        <CommandGroup heading={t("links")}>
          {links.map((link) => {
            const label = link.label ?? tLinks(link.key);
            return (
              <CommandItem
                key={link.key}
                value={`link ${label}`}
                onSelect={() => openExternal(link.href)}
              >
                <ExternalLink aria-hidden />
                {label}
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </Command>
  );
}
