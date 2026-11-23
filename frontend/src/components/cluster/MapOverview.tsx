"use client";

import { CLOUD_META } from "@/components/finops/format";
import { CloudStateBadge } from "@/components/ui/cloud-state-badge";
import type { Emergency } from "@/lib/api";
import { ArrowRight, Loader2, Lock, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { ClusterGlyph } from "./ClusterGlyph";

type Cloud = "onprem" | "aws" | "gcp";

// Node anchor: x in % of the width, y in px; flows reach the glyph centre.
const POS: Record<Cloud, { x: number; y: number }> = {
  onprem: { x: 50, y: 16 },
  aws: { x: 17, y: 236 },
  gcp: { x: 83, y: 236 },
};
const GLYPH_CY = 32;
const HEIGHT = 410;

/** Clusters as nodes, projects as pills, each running move as an animated flow between two nodes. */
export function MapOverview({ data }: { data: Emergency }) {
  const t = useTranslations("cluster");
  const tState = useTranslations("placement.state");
  const clouds = data.clusters.filter((c): c is { cloud: Cloud; state: string } => c.cloud in POS);
  const flows = data.moves.filter(
    (m) => ["running", "cancelling"].includes(m.status) && m.source in POS && m.target in POS,
  );
  const movingNames = new Set(flows.map((m) => m.project));

  return (
    <div className="overflow-x-auto rounded-xl border bg-card [background-image:radial-gradient(circle_at_1px_1px,var(--border)_1px,transparent_0)] [background-size:22px_22px]">
      <div className="relative min-w-[720px]" style={{ height: HEIGHT }}>
        <svg aria-hidden className="pointer-events-none absolute inset-0 size-full" viewBox={`0 0 100 ${HEIGHT}`} preserveAspectRatio="none">
          {flows.map((m) => {
            const a = POS[m.source as Cloud];
            const b = POS[m.target as Cloud];
            const y1 = a.y + GLYPH_CY;
            const y2 = b.y + GLYPH_CY;
            const cy = Math.min(y1, y2) - 70;
            return (
              <path
                key={m.id}
                d={`M${a.x} ${y1} Q${(a.x + b.x) / 2} ${cy} ${b.x} ${y2}`}
                vectorEffect="non-scaling-stroke"
                className="cluster-flow fill-none stroke-primary"
                strokeWidth={2.5}
                strokeDasharray="6 6"
              />
            );
          })}
        </svg>
        {flows.map((m) => {
          const a = POS[m.source as Cloud];
          const b = POS[m.target as Cloud];
          const y1 = a.y + GLYPH_CY;
          const y2 = b.y + GLYPH_CY;
          const cy = Math.min(y1, y2) - 70;
          return (
            <span
              key={m.id}
              className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border bg-card px-2.5 py-1 text-xs font-medium shadow-sm"
              style={{ left: `${(a.x + 2 * ((a.x + b.x) / 2) + b.x) / 4}%`, top: (y1 + 2 * cy + y2) / 4 }}
            >
              <span className="inline-flex items-center gap-1.5">
                <Loader2 aria-hidden className="size-3 motion-safe:animate-spin" />
                {m.project}
                <ArrowRight aria-hidden className="size-3 text-muted-foreground" />
                {CLOUD_META[m.target as Cloud].label}
              </span>
            </span>
          );
        })}
        {clouds.map((c) => {
          const projects = data.projects.filter((p) => p.cloud === c.cloud);
          return (
            <div
              key={c.cloud}
              className="absolute grid w-56 -translate-x-1/2 justify-items-center gap-2 text-center"
              style={{ left: `${POS[c.cloud].x}%`, top: POS[c.cloud].y }}
            >
              <ClusterGlyph cloud={c.cloud} state={c.state} size="lg" />
              <span className="mt-1 flex items-center gap-2 font-heading font-semibold">
                {CLOUD_META[c.cloud].label}
                <CloudStateBadge state={c.state}>{tState.has(c.state) ? tState(c.state) : c.state}</CloudStateBadge>
              </span>
              <span className="flex flex-wrap justify-center gap-1.5">
                {projects.length === 0 ? (
                  <span className="text-xs text-muted-foreground">{t("noProject")}</span>
                ) : (
                  projects.map((p) => (
                    <Link
                      key={p.name}
                      href={`/projects/${encodeURIComponent(p.name)}`}
                      className="inline-flex items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-xs font-semibold hover:border-primary/50 focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      {p.favorite && <Star role="img" aria-label={t("favorite")} className="size-3 fill-amber-500 text-amber-500" />}
                      {p.name}
                      {p.protected && <Lock role="img" aria-label={t("protected")} className="size-3 text-muted-foreground" />}
                      {movingNames.has(p.name) && <Loader2 aria-hidden className="size-3 motion-safe:animate-spin" />}
                    </Link>
                  ))
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
