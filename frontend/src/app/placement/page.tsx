"use client";

import { ClusterCard } from "@/components/cluster/ClusterCard";
import { ProjectRow } from "@/components/cluster/ProjectRow";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import CountUp from "@/components/ui/CountUp";
import { getPlatformPlacement, type PlatformPlacement } from "@/lib/api";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { ExternalLink, Map as MapIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const POLL_MS = 5000;

export default function PlacementPage() {
  const t = useTranslations("placement");
  const isAdmin = usePlatformAdmin();
  const [data, setData] = useState<PlatformPlacement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    const load = () =>
      getPlatformPlacement()
        .then((d) => {
          setData(d);
          setFailed(false);
        })
        .catch(() => setFailed(true));
    void load();
    return pollWhileVisible(load, POLL_MS);
  }, [isAdmin]);

  const clusters = data?.clusters ?? [];
  const names = new Set(clusters.flatMap((c) => c.projects.map((p) => p.name)));
  const movingNow = new Set(
    clusters.flatMap((c) =>
      c.projects.filter((p) => p.moving_to).map((p) => p.name),
    ),
  ).size;

  return (
    <div className="min-h-screen">
      <PageHeader title={t("title")} icon={MapIcon} />
      <PageBody>
        {!isAdmin ? null : (
          <div className="space-y-5">
            {failed && (
              <p role="alert" className="text-sm text-destructive">
                {t("loadFailed")}
              </p>
            )}
            <div className="flex flex-wrap gap-3">
              <Metric label={t("clustersUp")}>
                <CountUp trigger="load" to={clusters.filter((c) => c.state === "up").length} />
                {` / ${clusters.length}`}
              </Metric>
              <Metric label={t("projects")}>
                <CountUp trigger="load" to={names.size} />
              </Metric>
              <Metric label={t("movingNow")}>
                <CountUp trigger="load" to={movingNow} />
              </Metric>
            </div>
            <div className="grid gap-4 lg:grid-cols-3">
              {clusters.map((c) => (
                <ClusterCard
                  key={c.cloud}
                  cloud={c.cloud}
                  state={c.state}
                  memoryPct={c.memory_requested_pct}
                  footer={
                    <a href={c.console_url} target="_blank" rel="noopener noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
                      {t(`console.${c.cloud}`)}
                      <ExternalLink aria-hidden />
                    </a>
                  }
                >
                  {c.projects.length > 0
                    ? c.projects.map((p) => (
                        <ProjectRow
                          key={p.name}
                          name={p.name}
                          href={`/projects/${encodeURIComponent(p.name)}`}
                          moving={p.moving_to !== null}
                          apps={
                            <span className="text-xs text-muted-foreground">
                              {p.moving_from !== null ? `${t("arrivingBadge")} · ` : ""}
                              {t("apps", { count: p.apps })}
                            </span>
                          }
                        />
                      ))
                    : undefined}
                </ClusterCard>
              ))}
            </div>
          </div>
        )}
      </PageBody>
    </div>
  );
}

function Metric({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-32 flex-col gap-0.5 rounded-xl border bg-card px-4 py-2.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xl font-semibold tabular-nums">{children}</span>
    </div>
  );
}
