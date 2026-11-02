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
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { Boxes, ExternalLink, FolderKanban } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import {
  ADMIN_LINKS,
  ALL_PAGES,
  PUBLIC_LINKS,
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

function PaletteBody({ close }: { close: () => void }) {
  const t = useTranslations("shell.palette");
  const tNav = useTranslations("shell.sidebar");
  const tLinks = useTranslations("shell.links");
  const router = useRouter();
  const isAdmin = usePlatformAdmin();
  const { projects, apps } = useVisibleWork(0);

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

        <CommandGroup heading={t("pages")}>
          {ALL_PAGES.map(({ key, href, icon: Icon }) => (
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
            {projects.map((project) => (
              <CommandItem
                key={project.name}
                value={`project ${project.name}`}
                onSelect={() => go(`/projects/${project.name}`)}
              >
                <FolderKanban aria-hidden />
                <span className="capitalize">{project.name}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {apps.length > 0 && (
          <CommandGroup heading={t("apps")}>
            {apps.map((app) => (
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
            ))}
          </CommandGroup>
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
