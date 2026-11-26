"use client";

import { AppChip, appKind } from "@/components/cluster/AppChip";
import { ClusterCard } from "@/components/cluster/ClusterCard";
import { FootprintLine, FootprintTotal } from "@/components/cluster/Footprint";
import { MovePopover } from "@/components/cluster/MovePopover";
import { ProjectRow } from "@/components/cluster/ProjectRow";
import type { Emergency } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CircleCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

type Cloud = "onprem" | "aws" | "gcp";

const BUSY = ["running", "failed", "cancelling"];

function Tile({ href, label, value, tone }: { href: string; label: string; value: number; tone: "bad" | "warn" }) {
  return (
    <a
      href={href}
      className={cn(
        "flex min-w-36 flex-1 flex-col gap-0.5 rounded-xl border bg-card px-4 py-2.5 hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-ring",
        value > 0 && tone === "bad" && "border-destructive/60 text-destructive",
        value > 0 && tone === "warn" && "border-amber-500/60",
      )}
    >
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xl font-semibold tabular-nums">{value}</span>
    </a>
  );
}

/** Outage summary and the projects on clusters that are not up, each with its Move action. */
export function EmergencyPanel({ data, memory }: { data: Emergency; memory: Record<string, number | null> }) {
  const t = useTranslations("emergency");
  const [started, setStarted] = useState<Set<string>>(new Set());
  const bad = data.clusters.filter((c) => c.state !== "up");
  const states = Object.fromEntries(data.clusters.map((c) => [c.cloud, c.state]));
  const { projects, moves } = data;
  const count = (status: string[]) => moves.filter((m) => status.includes(m.status)).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3">
        <Tile href="#act-now" label={t("clustersDown")} value={bad.length} tone="bad" />
        <Tile href="#act-now" label={t("projectsAffected")} value={projects.length} tone="bad" />
        <Tile href="#act-now" label={t("favoritesAffected")} value={projects.filter((p) => p.favorite).length} tone="bad" />
        <Tile href="#moves" label={t("movesActive")} value={count(["running", "cancelling"])} tone="warn" />
        <Tile href="#moves" label={t("movesFailed")} value={count(["failed"])} tone="bad" />
      </div>

      <section id="act-now" aria-labelledby="act-now-title" className="scroll-mt-6 space-y-2">
        <h2 id="act-now-title" className="text-sm font-semibold">
          {t("actNow")}
        </h2>
        {projects.length === 0 && (
          <p className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
            <CircleCheck aria-hidden className="size-4 text-emerald-600" />
            {t("actNowEmpty")}
          </p>
        )}
        <div className="grid gap-3 lg:grid-cols-2">
          {bad.map((c) => {
            const here = projects
              .filter((p) => p.cloud === c.cloud)
              .sort((a, b) => Number(b.favorite) - Number(a.favorite));
            return (
              <ClusterCard key={c.cloud} cloud={c.cloud as Cloud} state={c.state} memoryPct={memory[c.cloud]}>
                {here.length > 0
                  ? here.map((p) => {
                      const busy =
                        started.has(p.name) || moves.some((m) => m.project === p.name && BUSY.includes(m.status));
                      return (
                        <ProjectRow
                          key={p.name}
                          name={p.name}
                          href={`/projects/${encodeURIComponent(p.name)}`}
                          favorite={p.favorite}
                          protectedProject={p.protected}
                          moving={busy}
                          total={<FootprintTotal rows={p.footprint} up={p.cloud_state === "up"} />}
                          apps={p.apps.map((a) => {
                            const row = p.footprint.find((f) => f.app === a);
                            return (
                              <AppChip
                                key={a}
                                name={a}
                                kind={appKind(a)}
                                state={p.cloud_state}
                                favorite={p.favorite_apps.includes(a)}
                                footprint={row && <FootprintLine row={row} />}
                              />
                            );
                          })}
                          action={
                            busy ? undefined : (
                              <MovePopover
                                project={p.name}
                                cloud={p.cloud as Cloud}
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
      </section>
    </div>
  );
}
