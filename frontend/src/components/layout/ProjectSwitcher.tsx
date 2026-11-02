"use client";

import { CreateProjectModal } from "@/components/projects/CreateProjectModal";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  Command,
} from "@/components/ui/command";
import { useFavorites } from "@/lib/favorites";
import { useProjects } from "@/lib/hooks";
import { can } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { ArrowLeft, ChevronsUpDown, FolderKanban, Plus, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  PROJECT_SECTIONS,
  projectHref,
  projectSegmentFromPath,
} from "./projectSections";
import { readRecentProjects } from "./recentProjects";

export function ProjectSwitcher({
  projectName,
  collapsed,
  onNavigate,
}: {
  projectName: string;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const t = useTranslations("projectNav");
  const tFav = useTranslations("favorites");
  const favorites = useFavorites();
  const router = useRouter();
  const pathname = usePathname();
  const { projects, refresh } = useProjects();
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const current = projects.find((p) => p.name === projectName);
  const cloudLabel = current ? t(`cloud.${current.target_cloud}`) : "";

  const { favoriteProjects, otherProjects } = useMemo(() => {
    if (!open) return { favoriteProjects: [], otherProjects: [] };
    const recent = readRecentProjects();
    const rank = (name: string) => {
      const i = recent.indexOf(name);
      return i === -1 ? Number.MAX_SAFE_INTEGER : i;
    };
    const sorted = projects
      .filter((p) => p.status !== "decommissioning")
      .sort((a, b) => rank(a.name) - rank(b.name));
    return {
      favoriteProjects: sorted.filter((p) => favorites.projects.includes(p.name)),
      otherProjects: sorted.filter((p) => !favorites.projects.includes(p.name)),
    };
  }, [open, projects, favorites.projects]);

  const go = (href: string) => {
    setOpen(false);
    onNavigate?.();
    router.push(href);
  };

  const targetFor = (name: string, role: Parameters<typeof can>[0]) => {
    const segment = projectSegmentFromPath(pathname);
    const section = Object.values(PROJECT_SECTIONS).find(
      (s) => s.segment === segment,
    );
    const allowed =
      section && (!section.capability || can(role, section.capability));
    return projectHref(name, allowed ? segment : "");
  };

  const projectItem = (p: (typeof projects)[number], favorite: boolean) => (
    <CommandItem
      key={p.name}
      value={`project ${p.name}`}
      onSelect={() => go(targetFor(p.name, p.role))}
    >
      {favorite ? <Star aria-hidden /> : <FolderKanban aria-hidden />}
      <span className="flex-1 truncate capitalize">{p.name}</span>
      <span className="text-xs text-muted-foreground">
        {t(`cloud.${p.target_cloud}`)}
      </span>
    </CommandItem>
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={collapsed ? projectName : t("switchProject")}
        title={projectName}
        className={cn(
          "flex w-full items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-left transition-colors hover:bg-accent",
          collapsed && "justify-center",
        )}
      >
        <FolderKanban className="h-4 w-4 shrink-0 text-primary" aria-hidden />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium capitalize">
                {projectName}
              </span>
              {cloudLabel && (
                <span className="block truncate text-xs text-muted-foreground">
                  {cloudLabel}
                </span>
              )}
            </span>
            <ChevronsUpDown
              className="h-4 w-4 shrink-0 text-muted-foreground"
              aria-hidden
            />
          </>
        )}
      </button>

      <CommandDialog open={open} onOpenChange={setOpen} title={t("switchProject")}>
        <Command>
          <CommandInput placeholder={t("searchProjects")} />
          <CommandList>
            <CommandEmpty>{t("noProject")}</CommandEmpty>
            <CommandGroup>
              <CommandItem value="back platform" onSelect={() => go("/")}>
                <ArrowLeft aria-hidden />
                {t("back")}
              </CommandItem>
            </CommandGroup>
            {favoriteProjects.length > 0 && (
              <CommandGroup heading={tFav("title")}>
                {favoriteProjects.map((p) => projectItem(p, true))}
              </CommandGroup>
            )}
            {otherProjects.length > 0 && (
              <CommandGroup heading={t("projects")}>
                {otherProjects.map((p) => projectItem(p, false))}
              </CommandGroup>
            )}
            <CommandGroup>
              <CommandItem
                value="new project"
                onSelect={() => {
                  setOpen(false);
                  setCreateOpen(true);
                }}
              >
                <Plus aria-hidden />
                {t("newProject")}
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </CommandDialog>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => refresh()}
      />
    </>
  );
}
