"use client";

import { StatTile } from "@/components/data/StatTile";
import { CLOUD_META } from "@/components/finops/format";
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
            <div className="grid gap-3 sm:grid-cols-3">
              <StatTile
                label={t("clustersUp")}
                loading={!data}
                value={
                  <>
                    <CountUp trigger="load" to={clusters.filter((c) => c.state === "up").length} />
                    {` / ${clusters.length}`}
                  </>
                }
              />
              <StatTile
                label={t("projects")}
                loading={!data}
                value={<CountUp trigger="load" to={names.size} />}
              />
              <StatTile
                label={t("movingNow")}
                loading={!data}
                value={<CountUp trigger="load" to={movingNow} />}
              />
            </div>
            <div className="grid gap-4 lg:grid-cols-3">
              {!data &&
                (["onprem", "aws", "gcp"] as const).map((cloud) => (
                  <div key={cloud} className="space-y-3 rounded-xl border bg-card p-4" aria-busy>
                    <div className="flex items-center justify-between">
                      <b style={{ color: CLOUD_META[cloud].color }}>{CLOUD_META[cloud].label}</b>
                      <div className="h-4 w-12 animate-pulse rounded bg-muted" />
                    </div>
                    <div className="h-2 animate-pulse rounded bg-muted" />
                    <div className="space-y-2">
                      {Array.from({ length: cloud === "onprem" ? 3 : 1 }, (_, i) => (
                        <div key={i} className="h-10 animate-pulse rounded-lg bg-muted" />
                      ))}
                    </div>
                  </div>
                ))}
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
