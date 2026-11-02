"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { CircleHelp, ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import { ADMIN_LINKS, PUBLIC_LINKS } from "./shellData";

export function LinksMenu({ collapsed }: { collapsed: boolean }) {
  const t = useTranslations("shell.links");
  const isAdmin = usePlatformAdmin();

  return (
    <DropdownMenu className="block w-full">
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={t("label")}
          aria-label={collapsed ? t("label") : undefined}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground/80 transition-colors hover:bg-accent hover:text-foreground",
            collapsed && "justify-center",
          )}
        >
          <CircleHelp className="h-4 w-4 shrink-0" aria-hidden />
          {!collapsed && <span className="truncate">{t("label")}</span>}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-60">
        {PUBLIC_LINKS.map((link) => (
          <DropdownMenuItem key={link.key} asChild>
            <a href={link.href} target="_blank" rel="noopener noreferrer">
              <span className="flex-1 truncate">{t(link.key)}</span>
              <ExternalLink
                className="h-3 w-3 shrink-0 text-muted-foreground"
                aria-hidden
              />
            </a>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        {isAdmin ? (
          <>
            <DropdownMenuLabel className="text-[11.5px] font-medium text-muted-foreground">
              {t("adminGroup")}
            </DropdownMenuLabel>
            {ADMIN_LINKS.map((link) => (
              <DropdownMenuItem key={link.key} asChild>
                <a href={link.href} target="_blank" rel="noopener noreferrer">
                  <span className="flex-1 truncate">{link.label}</span>
                  <ExternalLink
                    className="h-3 w-3 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                </a>
              </DropdownMenuItem>
            ))}
          </>
        ) : (
          <p className="px-2 py-1.5 text-xs text-muted-foreground">
            {t("adminOnly", { count: ADMIN_LINKS.length })}
          </p>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
