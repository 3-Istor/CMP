"use client";

import { CLOUD_META } from "@/components/finops/format";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import CountUp from "@/components/ui/CountUp";
import ShinyText from "@/components/ui/ShinyText";
import SpotlightCard from "@/components/ui/SpotlightCard";
import { getPlatformPlacement, type PlatformPlacement } from "@/lib/api";
import { useIsDark } from "@/lib/theme";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { cn } from "@/lib/utils";
import { ExternalLink, Map as MapIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

const POLL_MS = 5000;

export default function PlacementPage() {
  const t = useTranslations("placement");
  const isAdmin = usePlatformAdmin();
  const theme = useIsDark() ? "dark" : "light";
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
                <SpotlightCard
                  key={c.cloud}
                  theme={theme}
                  style={
                    {
                      "--spotlight-card-surface": "var(--card)",
                      "--spotlight-card-border": "var(--border)",
                    } as React.CSSProperties
                  }
                  spotlightColor={CLOUD_META[c.cloud].color}
                  aria-label={CLOUD_META[c.cloud].label}
                  className="flex flex-col gap-3.5 text-card-foreground"
                >
                  <div className="flex items-start justify-between gap-2">
                    <h2 className="flex items-center gap-2 text-base font-semibold">
                      <span
                        aria-hidden
                        className="size-3 rounded"
                        style={{ background: CLOUD_META[c.cloud].color }}
                      />
                      {CLOUD_META[c.cloud].label}
                    </h2>
                    <Badge variant={c.state === "up" ? "secondary" : "destructive"}>
                      {t.has(`state.${c.state}`) ? t(`state.${c.state}`) : c.state}
                    </Badge>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>
                        {t("memory")} · {t("nodes", { count: c.nodes })}
                      </span>
                      <span className="font-semibold text-foreground">
                        {c.memory_requested_pct == null ? t("unread") : `${c.memory_requested_pct} %`}
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${c.memory_requested_pct ?? 0}%`,
                          background: CLOUD_META[c.cloud].color,
                        }}
                      />
                    </div>
                  </div>
                  <a href={c.console_url} target="_blank" rel="noopener noreferrer" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "self-start")}>
                    {t(`console.${c.cloud}`)}
                    <ExternalLink aria-hidden />
                  </a>
                  <ul className="flex flex-col gap-2">
                    {c.projects.map((p) => {
                      const leaving = p.moving_to !== null;
                      const arriving = p.moving_from !== null;
                      return (
                        <li key={p.name}>
                          <Link
                            href={`/projects/${encodeURIComponent(p.name)}`}
                            className={cn(
                              "flex flex-col gap-1 rounded-lg border bg-card p-3 hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-ring",
                              leaving && "opacity-75",
                              arriving && "border-dashed",
                            )}
                            style={arriving ? { borderColor: CLOUD_META[c.cloud].color } : undefined}
                          >
                            <span className="flex items-center justify-between gap-2">
                              <span className="text-sm font-semibold">{p.name}</span>
                              <Badge variant="secondary">
                                {leaving ? (
                                  <ShinyText text={t("moving")} />
                                ) : arriving ? (
                                  t("arrivingBadge")
                                ) : p.apps === 0 ? (
                                  t("emptyProject")
                                ) : (
                                  t("running")
                                )}
                              </Badge>
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {t("apps", { count: p.apps })}
                            </span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </SpotlightCard>
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
