"use client";

import {
  useDeploymentsList,
  useProjects,
} from "@/lib/hooks";
import {
  BellRing,
  Bot,
  Boxes,
  FolderKanban,
  History,
  Home,
  Shield,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export const DOCS_URL = "https://3-istor.github.io/cnp-docs/";

export type PageKey =
  | "home"
  | "projects"
  | "apps"
  | "finops"
  | "security"
  | "activity"
  | "alerts"
  | "assistants";

export interface PageEntry {
  key: PageKey;
  href: string;
  icon: LucideIcon;
  isActive: (pathname: string) => boolean;
}

export const HOME_PAGE: PageEntry = {
  key: "home",
  href: "/",
  icon: Home,
  isActive: (pathname) => pathname === "/",
};

export const WORK_PAGES: PageEntry[] = [
  {
    key: "projects",
    href: "/projects",
    icon: FolderKanban,
    isActive: (pathname) =>
      pathname.startsWith("/projects") && !pathname.includes("/apps/"),
  },
  {
    key: "apps",
    href: "/apps",
    icon: Boxes,
    isActive: (pathname) =>
      pathname === "/apps" || pathname.includes("/apps/"),
  },
];

export const PLATFORM_PAGES: PageEntry[] = [
  {
    key: "finops",
    href: "/finops",
    icon: Wallet,
    isActive: (pathname) => pathname.startsWith("/finops"),
  },
  {
    key: "security",
    href: "/security",
    icon: Shield,
    isActive: (pathname) => pathname.startsWith("/security"),
  },
  {
    key: "activity",
    href: "/activity",
    icon: History,
    isActive: (pathname) => pathname.startsWith("/activity"),
  },
  {
    key: "alerts",
    href: "/alerts",
    icon: BellRing,
    isActive: (pathname) => pathname.startsWith("/alerts"),
  },
  {
    key: "assistants",
    href: "/connect-ai",
    icon: Bot,
    isActive: (pathname) => pathname.startsWith("/connect-ai"),
  },
];

export const ALL_PAGES: PageEntry[] = [
  HOME_PAGE,
  ...WORK_PAGES,
  ...PLATFORM_PAGES,
];

export type ExternalLinkKey =
  | "status"
  | "roadmap"
  | "docs"
  | "argocd"
  | "grafana"
  | "vault"
  | "keycloak"
  | "n8n";

export interface ExternalLinkEntry {
  key: ExternalLinkKey;
  href: string;
  label?: string;
}

export const PUBLIC_LINKS: ExternalLinkEntry[] = [
  { key: "status", href: "https://status.3istor.com" },
  { key: "roadmap", href: "https://roadmap.3istor.com" },
  { key: "docs", href: DOCS_URL },
];

export const ADMIN_LINKS: ExternalLinkEntry[] = [
  { key: "argocd", href: "https://argocd.3istor.com", label: "Argo CD" },
  { key: "grafana", href: "https://grafana.3istor.com", label: "Grafana" },
  { key: "vault", href: "https://vault.3istor.com", label: "Vault" },
  { key: "keycloak", href: "https://auth.3istor.com/admin/", label: "Keycloak" },
  { key: "n8n", href: "https://n8n.3istor.com", label: "n8n" },
];

// Only deployments that belong to a visible project and are not deleted are
// linkable, so a stale response cannot surface a link into a project the user
// cannot open.
export function useVisibleWork(projectsIntervalMs = 15000) {
  const { projects, loading: loadingProjects } = useProjects(projectsIntervalMs);
  const { deployments, loading: loadingApps } = useDeploymentsList(15000, 30000);

  const apps = deployments.filter(
    (d) =>
      d.project_id &&
      d.status !== "deleted" &&
      projects.some((p) => p.name === d.project_id),
  );

  return { projects, apps, loading: loadingProjects || loadingApps };
}
