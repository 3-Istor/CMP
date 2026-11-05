"use client";

import type { ComponentType } from "react";
import { useTranslations } from "next-intl";
import type { ProjectLink } from "@/types";
import {
  Activity,
  AlarmClock,
  BarChart3,
  ExternalLink,
  FileCode,
  KeyRound,
  Play,
  Vault,
} from "lucide-react";
import { Github } from "@/components/icons/Github";

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  argocd_project: FileCode,
  argocd_component: FileCode,
  status: Activity,
  offhours: AlarmClock,
  vault: Vault,
  keycloak: KeyRound,
  keycloak_staging: KeyRound,
  grafana_org: BarChart3,
  github_repo: Github,
  github_actions: Play,
};

export function linkLabel(
  link: ProjectLink,
  t: ReturnType<typeof useTranslations>,
): string {
  const key = link.label_key.replace(/^links\./, "");
  const base = t.has(key) ? t(key) : link.label_key;
  if (link.id === "argocd_component" && link.component) {
    return `${base} · ${link.component}`;
  }
  if (link.id === "github_repo" && link.app) {
    return `${base} · ${link.app}`;
  }
  return base;
}

export function linkIcon(link: ProjectLink): ComponentType<{ className?: string }> {
  return ICONS[link.id] ?? ExternalLink;
}
