"use client";

import { StateLabel } from "@/components/projects/DeploymentHealth";
import { useAppHealth } from "@/lib/hooks";
import type { ProjectLink } from "@/types";
import { ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import { OverviewCard } from "./OverviewCard";

export function ComponentsCard({
  appId,
  links,
}: {
  appId: number;
  links: ProjectLink[];
}) {
  const t = useTranslations("appPage.components");
  const th = useTranslations("health");
  const tc = useTranslations("appPage.common");
  const { health, loading, error, refresh } = useAppHealth(appId, 10_000);
  const components = health?.components ?? [];
  const argoLinks = new Map(
    links
      .filter((l) => l.id === "argocd_component" && l.component)
      .map((l) => [l.component as string, l.url]),
  );

  return (
    <OverviewCard
      title={t("title")}
      state={{
        loading,
        error,
        stale: error !== null && health !== null,
        updatedAt: null,
        hasData: health !== null,
        empty: health !== null && components.length === 0,
        retry: refresh,
      }}
      emptyText={th("noComponents")}
      skeletonClassName="h-32 w-full"
    >
      <ul className="divide-y">
        {components.map((c) => {
          const argocd = [c.argocd_sync, c.argocd_health].filter(Boolean);
          const name = th.has(`components.${c.name}`)
            ? th(`components.${c.name}`)
            : c.name;
          const url = argoLinks.get(c.name);
          return (
            <li
              key={c.name}
              className="grid grid-cols-2 items-center gap-x-4 gap-y-1 py-2 text-sm first:pt-0 last:pb-0 sm:grid-cols-[1fr_auto_auto_auto_auto]"
            >
              <span className="font-medium">{name}</span>
              <StateLabel state={c.state} />
              <span className="font-mono text-xs text-muted-foreground">
                {th("replicas")} {c.ready}/{c.desired}
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                {th("restarts")} {c.restarts_15m ?? "-"}
              </span>
              <span className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                {argocd.length > 0 ? argocd.join(" / ") : th("argocdUnknown")}
                {url && (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={tc("openArgo", { name })}
                    title={tc("openArgo", { name })}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  </a>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </OverviewCard>
  );
}
