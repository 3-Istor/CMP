"use client";

import {
  useDeploymentsList,
  useProjects,
} from "@/lib/hooks";
import { can } from "@/lib/permissions";
import {
  BellRing,
  Bot,
  Boxes,
  FolderKanban,
  History,
  Home,
  KeyRound,
  Map as MapIcon,
  MessageSquareWarning,
  Plug,
  Shield,
  UserCheck,
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
  | "agents"
  | "assistants"
  | "access"
  | "accessReviews"
  | "issueReports"
  | "clusters";

export interface PageEntry {
  key: PageKey;
  href: string;
  icon: LucideIcon;
  isActive: (pathname: string) => boolean;
  /** Shown only to members of one of these platform groups. */
  platformRoles?: string[];
  /** Hidden from finops and guest-only users: needs a member+ project or infra. */
  needsMemberProject?: true;
  /** security-auditor sees it without a project role. */
  auditorSees?: true;
}

export function isPageVisible(
  page: PageEntry,
  roles: string[],
  ctx: { hasMemberProject: boolean },
): boolean {
  if (page.platformRoles) return page.platformRoles.some((r) => roles.includes(r));
  return (
    !page.needsMemberProject ||
    ctx.hasMemberProject ||
    roles.includes("infra") ||
    (!!page.auditorSees && roles.includes("security-auditor"))
  );
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
    needsMemberProject: true,
    auditorSees: true,
  },
  {
    key: "activity",
    href: "/activity",
    icon: History,
    isActive: (pathname) => pathname.startsWith("/activity"),
    needsMemberProject: true,
    auditorSees: true,
  },
  {
    key: "alerts",
    href: "/alerts",
    icon: BellRing,
    isActive: (pathname) => pathname.startsWith("/alerts"),
    needsMemberProject: true,
    auditorSees: true,
  },
  {
    key: "agents",
    href: "/agents",
    icon: Bot,
    isActive: (pathname) => pathname === "/agents" || pathname.includes("/ai/agents/"),
    needsMemberProject: true,
  },
  {
    key: "assistants",
    href: "/connect-ai",
    icon: Plug,
    isActive: (pathname) => pathname.startsWith("/connect-ai"),
    needsMemberProject: true,
  },
  {
    key: "access",
    href: "/access",
    icon: KeyRound,
    isActive: (pathname) =>
      pathname === "/access" || pathname.startsWith("/access/"),
    platformRoles: ["infra"],
  },
  {
    key: "accessReviews",
    href: "/access-reviews",
    icon: UserCheck,
    isActive: (pathname) => pathname.startsWith("/access-reviews"),
    platformRoles: ["infra", "security-auditor"],
  },
  {
    key: "issueReports",
    href: "/issue-reports",
    icon: MessageSquareWarning,
    isActive: (pathname) => pathname.startsWith("/issue-reports"),
    platformRoles: ["infra"],
  },
  {
    key: "clusters",
    href: "/clusters",
    icon: MapIcon,
    isActive: (pathname) => pathname.startsWith("/clusters"),
    platformRoles: ["infra"],
  },
];

export const ALL_PAGES: PageEntry[] = [
  HOME_PAGE,
  ...WORK_PAGES,
  ...PLATFORM_PAGES,
];

export type ExternalLinkKey =
  | "status"
  | "docs"
  | "argocd"
  | "grafana"
  | "vault"
  | "keycloak";

export interface ExternalLinkEntry {
  key: ExternalLinkKey;
  href: string;
  label?: string;
}

export const PUBLIC_LINKS: ExternalLinkEntry[] = [
  { key: "status", href: "https://status.3istor.com" },
  { key: "docs", href: DOCS_URL },
];

export const ADMIN_LINKS: ExternalLinkEntry[] = [
  { key: "argocd", href: "https://argocd.3istor.com", label: "Argo CD" },
  { key: "grafana", href: "https://grafana.3istor.com", label: "Grafana" },
  { key: "vault", href: "https://vault.3istor.com", label: "Vault" },
  { key: "keycloak", href: "https://auth.3istor.com/admin/", label: "Keycloak" },
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

  const hasMemberProject = projects.some((p) => can(p.role, "activity.view"));

  return { projects, apps, hasMemberProject, loading: loadingProjects || loadingApps };
}
