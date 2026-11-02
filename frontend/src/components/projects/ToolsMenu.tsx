"use client";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { linkIcon, linkLabel } from "@/lib/links";
import type { ProjectLink } from "@/types";
import { ChevronDown, ExternalLink, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";
import { Fragment } from "react";

const GROUPS: ProjectLink["group"][] = ["view", "code", "admin"];

export function ToolsMenu({ links }: { links: ProjectLink[] }) {
  const ta = useTranslations("actions");
  const tl = useTranslations("links");

  const groups = GROUPS.map((group) => ({
    group,
    items: links.filter((link) => link.group === group),
  })).filter(({ items }) => items.length > 0);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={groups.length === 0}
          title={groups.length === 0 ? ta("toolsEmpty") : undefined}
        >
          <Wrench aria-hidden />
          {ta("tools")}
          <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[14rem]">
        {groups.map(({ group, items }, index) => (
          <Fragment key={group}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
              {ta(`toolsGroups.${group}`)}
            </DropdownMenuLabel>
            {items.map((link) => {
              const Icon = linkIcon(link);
              return (
                <DropdownMenuItem
                  key={`${link.id}:${link.app ?? ""}:${link.component ?? ""}`}
                  asChild
                  className="gap-2"
                >
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`${linkLabel(link, tl)}, ${ta("externalLink")}`}
                  >
                    <Icon className="h-4 w-4" />
                    <span className="flex-1">{linkLabel(link, tl)}</span>
                    <ExternalLink
                      className="h-3 w-3 text-muted-foreground"
                      aria-hidden
                    />
                  </a>
                </DropdownMenuItem>
              );
            })}
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
