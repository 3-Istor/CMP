import type { ComponentType } from "react";
import { buttonVariants } from "@/components/ui/button";
import type { ProjectLink } from "@/types";
import {
  Activity,
  AlarmClock,
  BarChart3,
  ExternalLink,
  FileCode,
  Play,
  Vault,
} from "lucide-react";
import { Github } from "@/components/icons/Github";

const LABELS: Record<string, string> = {
  "links.argocd_project": "ArgoCD",
  "links.argocd_component": "ArgoCD",
  "links.status": "Status",
  "links.offhours": "Offhours",
  "links.vault": "Vault",
  "links.grafana_org": "Grafana",
  "links.github_repo": "Dépôt GitHub",
  "links.github_actions": "GitHub Actions",
};

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  argocd_project: FileCode,
  argocd_component: FileCode,
  status: Activity,
  offhours: AlarmClock,
  vault: Vault,
  grafana_org: BarChart3,
  github_repo: Github,
  github_actions: Play,
};

export function linkLabel(link: ProjectLink): string {
  const base = LABELS[link.label_key] ?? link.label_key;
  if (link.id === "argocd_component" && link.component) {
    return `${base} · ${link.component}`;
  }
  if (link.id === "github_repo" && link.app) {
    return `${base} · ${link.app}`;
  }
  return base;
}

export function LinkButtons({
  links,
  className,
}: {
  links: ProjectLink[];
  className?: string;
}) {
  return (
    <>
      {links.map((link) => {
        const Icon = ICONS[link.id] ?? ExternalLink;
        return (
          <a
            key={`${link.id}:${link.app ?? ""}:${link.component ?? ""}`}
            href={link.url}
            {...(link.external
              ? { target: "_blank", rel: "noopener noreferrer" }
              : {})}
            className={buttonVariants({ variant: "outline", size: "sm", className })}
          >
            <Icon className="mr-2 h-4 w-4" />
            {linkLabel(link)}
            {link.external && <ExternalLink className="ml-auto h-3 w-3" />}
          </a>
        );
      })}
    </>
  );
}
