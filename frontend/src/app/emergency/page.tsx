"use client";

import { CLOUD_META } from "@/components/finops/format";
import { AppChip, appKind } from "@/components/cluster/AppChip";
import { ClusterCard } from "@/components/cluster/ClusterCard";
import { MapOverview } from "@/components/cluster/MapOverview";
import { MovePopover } from "@/components/cluster/MovePopover";
import { FootprintLine, FootprintTotal } from "@/components/cluster/Footprint";
import { ProjectRow } from "@/components/cluster/ProjectRow";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Badge } from "@/components/ui/badge";
import { usePlacementMemory } from "@/components/cluster/useClusterStates";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { getEmergency, type Emergency } from "@/lib/api";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { cn } from "@/lib/utils";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { CircleCheck, Siren } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const POLL_MS = 5000;

const cloudLabel = (cloud: string) =>
  CLOUD_META[cloud as keyof typeof CLOUD_META]?.label ?? cloud;

const MOVE_STATUS: Record<string, string> = {
  failed: "bg-destructive/10 text-destructive",
  cancelling: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  running: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
};

function Tile({
  href,
  label,
  value,
  tone,
}: {
  href: string;
  label: string;
  value: number | null;
  tone?: "bad" | "warn";
}) {
  return (
    <a
      href={href}
      className={cn(
        "flex min-w-36 flex-1 flex-col gap-0.5 rounded-xl border bg-card px-4 py-2.5 hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-ring",
        (value ?? 0) > 0 && tone === "bad" && "border-destructive/60 text-destructive",
        (value ?? 0) > 0 && tone === "warn" && "border-amber-500/60",
      )}
    >
      <span className="text-xs text-muted-foreground">{label}</span>
      {value === null ? (
        <span className="my-0.5 h-6 w-10 animate-pulse rounded bg-muted" aria-hidden />
      ) : (
        <span className="text-xl font-semibold tabular-nums">{value}</span>
      )}
    </a>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
      <CircleCheck aria-hidden className="size-4 text-emerald-600" />
      {children}
    </p>
  );
}

export default function EmergencyPage() {
  const t = useTranslations("emergency");
  const tSteps = useTranslations("projectSettings.location.steps");
  const isAdmin = usePlatformAdmin();
  const [data, setData] = useState<Emergency | null>(null);
  const [failed, setFailed] = useState(false);
  const [started, setStarted] = useState<Set<string>>(new Set());
  const memory = usePlacementMemory();

  useEffect(() => {
    if (!isAdmin) return;
    const load = () =>
      getEmergency()
        .then((d) => {
          setData(d);
          setFailed(false);
        })
        .catch(() => setFailed(true));
    void load();
    return pollWhileVisible(load, POLL_MS);
  }, [isAdmin]);

  const clusters = data?.clusters ?? [];
  const bad = clusters.filter((c) => c.state !== "up");
  const states = Object.fromEntries(clusters.map((c) => [c.cloud, c.state]));
  const projects = data?.projects ?? [];
  const moves = data?.moves ?? [];
  const count = (status: string[]) => moves.filter((m) => status.includes(m.status)).length;
  const loading = !data;
  const moveColumns: DataColumn<(typeof moves)[number]>[] = [
    { key: "project", header: t("project"), sortable: true, className: "font-semibold", cell: (m) => m.project },
    {
      key: "route",
      header: t("route"),
      className: "whitespace-nowrap",
      cell: (m) => `${cloudLabel(m.source)} → ${cloudLabel(m.target)}`,
    },
    {
      key: "step",
      header: t("step"),
      cell: (m) => (m.step && tSteps.has(m.step) ? tSteps(m.step) : (m.step ?? "")),
    },
    {
      key: "status",
      header: t("status"),
      sortable: true,
      cell: (m) => (
        <span
          className={cn(
            "inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium",
            MOVE_STATUS[m.status] ?? "bg-muted text-muted-foreground",
          )}
        >
          {m.status}
        </span>
      ),
    },
    {
      key: "started_at",
      header: t("started"),
      sortable: true,
      sortValue: (m) => Date.parse(m.started_at),
      className: "whitespace-nowrap tabular-nums",
      cell: (m) => new Date(m.started_at).toLocaleString(),
    },
    {
      key: "trigger",
      header: t("trigger"),
      cell: (m) => (
        <Badge variant={m.auto ? "default" : "outline"}>{m.auto ? t("auto") : t("manual")}</Badge>
      ),
    },
  ];

  return (
    <div className="min-h-screen">
      <PageHeader title={t("title")} icon={Siren} />
      <PageBody>
        {!isAdmin ? null : (
          <div className="space-y-6">
            {failed && (
              <p role="alert" className="text-sm text-destructive">
                {t("loadFailed")}
              </p>
            )}

            <div className="flex flex-wrap gap-3">
              <Tile href="#clusters" label={t("clustersDown")} value={loading ? null : bad.length} tone="bad" />
              <Tile href="#act-now" label={t("projectsAffected")} value={loading ? null : projects.length} tone="bad" />
              <Tile
                href="#act-now"
                label={t("favoritesAffected")}
                value={loading ? null : projects.filter((p) => p.favorite).length}
                tone="bad"
              />
              <Tile href="#moves" label={t("movesActive")} value={loading ? null : count(["running", "cancelling"])} tone="warn" />
              <Tile href="#moves" label={t("movesFailed")} value={loading ? null : count(["failed"])} tone="bad" />
            </div>

            <section id="clusters" aria-labelledby="clusters-title" className="scroll-mt-6 space-y-2">
              <h2 id="clusters-title" className="text-sm font-semibold">
                {t("clustersTitle")}
              </h2>
              {data ? <MapOverview data={data} /> : <div className="h-64 animate-pulse rounded-xl bg-muted" aria-busy />}
            </section>

            <section id="act-now" aria-labelledby="act-now-title" className="scroll-mt-6 space-y-2">
              <h2 id="act-now-title" className="text-sm font-semibold">
                {t("actNow")}
              </h2>
              {loading ? (
                <div className="grid gap-3 lg:grid-cols-3" aria-busy>
                  {[0, 1, 2].map((i) => (
                    <div key={i} className="h-40 animate-pulse rounded-xl bg-muted" />
                  ))}
                </div>
              ) : projects.length === 0 ? (
                <Empty>{t("actNowEmpty")}</Empty>
              ) : (
                <div className="grid gap-3 lg:grid-cols-3">
                  {clusters.map((c) => {
                    const here = projects.filter((p) => p.cloud === c.cloud);
                    return (
                      <ClusterCard
                        key={c.cloud}
                        cloud={c.cloud as "onprem" | "aws" | "gcp"}
                        state={c.state}
                        memoryPct={memory[c.cloud]}
                      >
                        {here.length > 0
                          ? [...here]
                              .sort((a, b) => Number(b.favorite) - Number(a.favorite))
                              .map((p) => {
                                const busy =
                                  started.has(p.name) ||
                                  moves.some(
                                    (m) => m.project === p.name && ["running", "failed", "cancelling"].includes(m.status),
                                  );
                                return (
                                  <ProjectRow
                                    key={p.name}
                                    name={p.name}
                                    href={`/projects/${encodeURIComponent(p.name)}`}
                                    favorite={p.favorite}
                                    protectedProject={p.protected}
                                    moving={busy}
                                    total={<FootprintTotal rows={p.footprint} up={p.cloud_state === "up"} />}
                                    apps={p.apps.map((a) => (
                                      <AppChip
                                        key={a}
                                        name={a}
                                        kind={appKind(a)}
                                        state={p.cloud_state}
                                        favorite={p.favorite_apps.includes(a)}
                                        footprint={p.footprint.find((f) => f.app === a) && <FootprintLine row={p.footprint.find((f) => f.app === a)!} />}
                                      />
                                    ))}
                                    action={
                                      busy ? undefined : (
                                        <MovePopover
                                          project={p.name}
                                          cloud={p.cloud as "onprem" | "aws" | "gcp"}
                                          states={states}
                                          memory={memory}
                                          onStarted={() => setStarted((prev) => new Set(prev).add(p.name))}
                                        />
                                      )
                                    }
                                  />
                                );
                              })
                          : undefined}
                      </ClusterCard>
                    );
                  })}
                </div>
              )}
            </section>

            <section id="moves" aria-labelledby="moves-title" className="scroll-mt-6 space-y-2">
              <h2 id="moves-title" className="text-sm font-semibold">
                {t("movesTitle")}
              </h2>
              <DataTable
                columns={moveColumns}
                rows={moves}
                rowKey={(m) => m.id}
                loading={loading}
                storageKey="emergency.moves"
                rowClassName={(m) => (m.auto ? "bg-amber-500/10" : undefined)}
                empty={{ title: t("movesEmpty") }}
              />
            </section>
          </div>
        )}
      </PageBody>
    </div>
  );
}
