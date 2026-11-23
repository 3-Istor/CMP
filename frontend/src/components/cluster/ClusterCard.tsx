"use client";

import { CLOUD_META } from "@/components/finops/format";
import { CloudStateBadge } from "@/components/ui/cloud-state-badge";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { ClusterGlyph, toneOf, type GlyphCloud } from "./ClusterGlyph";

const SUB: Record<string, string> = { onprem: "K3s · hub", aws: "EKS", gcp: "GKE" };

/** Cluster card: glyph, state, memory bar, then the projects it runs (children). */
export function ClusterCard({
  cloud,
  state,
  memoryPct,
  children,
  footer,
  className,
}: {
  cloud: Exclude<GlyphCloud, "public">;
  state: string;
  memoryPct?: number | null;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}) {
  const t = useTranslations("cluster");
  const tState = useTranslations("placement.state");
  const tone = toneOf(state);
  return (
    <article
      aria-label={CLOUD_META[cloud].label}
      data-state={state}
      className={cn(
        "grid min-w-0 grid-rows-[auto_auto_1fr_auto] overflow-hidden rounded-xl border bg-card text-card-foreground",
        tone === "down" && "border-destructive/50",
        tone === "degraded" && "border-amber-500/50",
        className,
      )}
    >
      <header className="flex items-center gap-3 px-4 pt-4 pb-2.5">
        <ClusterGlyph cloud={cloud} state={state} className="mx-0.5" />
        <div className="grid min-w-0 leading-tight">
          <span className="font-heading font-semibold">{CLOUD_META[cloud].label}</span>
          <span className="text-xs text-muted-foreground">{SUB[cloud]}</span>
        </div>
        <CloudStateBadge state={state} className="ml-auto">
          {tState.has(state) ? tState(state) : state}
        </CloudStateBadge>
      </header>
      <div className="grid gap-1 px-4 pb-3 text-xs text-muted-foreground">
        {tone === "down" ? (
          <span>{t("unreachable")}</span>
        ) : memoryPct == null ? null : (
          <>
            <span>{t("memory", { pct: Math.round(memoryPct) })}</span>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full" style={{ width: `${Math.min(100, memoryPct)}%`, background: CLOUD_META[cloud].color }} />
            </div>
          </>
        )}
      </div>
      <div
        className={cn(
          "grid content-start gap-2 border-t p-3",
          tone === "down" && "bg-[repeating-linear-gradient(-45deg,transparent_0_8px,color-mix(in_srgb,var(--destructive)_6%,transparent)_8px_16px)]",
        )}
      >
        {children ?? <span className="px-1 py-1 text-xs text-muted-foreground">{t("noProject")}</span>}
      </div>
      {footer && <div className="border-t p-3">{footer}</div>}
    </article>
  );
}
