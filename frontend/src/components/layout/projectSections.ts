import type { Capability } from "@/lib/permissions";
import {
  BellRing,
  Bot,
  Settings,
  History,
  LayoutDashboard,
  Network,
  Shield,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export type ProjectSectionKey =
  | "overview"
  | "topology"
  | "members"
  | "costs"
  | "security"
  | "activity"
  | "alerts"
  | "assistants"
  | "settings";

export interface ProjectSectionEntry {
  key: ProjectSectionKey;
  segment: string;
  icon: LucideIcon;
  capability?: Capability;
}

export const PROJECT_SECTIONS: Record<ProjectSectionKey, ProjectSectionEntry> = {
  overview: { key: "overview", segment: "", icon: LayoutDashboard },
  topology: {
    key: "topology",
    segment: "topology",
    icon: Network,
    capability: "topology.view",
  },
  members: {
    key: "members",
    segment: "members",
    icon: Users,
    capability: "members.view",
  },
  costs: {
    key: "costs",
    segment: "costs",
    icon: Wallet,
    capability: "finops.view",
  },
  security: {
    key: "security",
    segment: "security",
    icon: Shield,
    capability: "app.viewObservability",
  },
  activity: {
    key: "activity",
    segment: "activity",
    icon: History,
    capability: "app.viewObservability",
  },
  alerts: {
    key: "alerts",
    segment: "alerts",
    icon: BellRing,
    capability: "app.viewObservability",
  },
  assistants: { key: "assistants", segment: "assistants", icon: Bot },
  settings: {
    key: "settings",
    segment: "settings",
    icon: Settings,
    capability: "app.viewObservability",
  },
};

export function projectHref(projectName: string, segment = ""): string {
  const base = `/projects/${encodeURIComponent(projectName)}`;
  return segment ? `${base}/${segment}` : base;
}

export function projectFromPath(pathname: string | null): string | null {
  const parts = (pathname ?? "").split("/");
  if (parts[1] !== "projects" || !parts[2]) return null;
  return decodeURIComponent(parts[2]);
}

export function projectSegmentFromPath(pathname: string): string {
  const parts = pathname.split("/");
  return parts[3] && parts[3] !== "apps" ? parts[3] : "";
}
