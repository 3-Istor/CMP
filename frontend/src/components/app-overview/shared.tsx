"use client";

import { compactDuration, type DurationUnits } from "@/lib/appHealth";
import type { AppHealthStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { forwardRef } from "react";

export const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

/** A titled block of the overview: the title and description stay visible while the content loads. */
export const Panel = forwardRef<
  HTMLElement,
  {
    title: React.ReactNode;
    description?: React.ReactNode;
    action?: React.ReactNode;
    className?: string;
    children: React.ReactNode;
  }
>(function Panel({ title, description, action, className, children }, ref) {
  return (
    <section ref={ref} className={cn("flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4", className)}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
      </header>
      {children}
    </section>
  );
});

export function SkeletonLines({ count, className }: { count: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} aria-busy>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded bg-muted"
          style={{ width: `${60 + ((i * 37) % 35)}%` }}
        />
      ))}
    </div>
  );
}

const DOT: Record<AppHealthStatus, string> = {
  healthy: "bg-emerald-500",
  degraded: "bg-amber-500",
  down: "bg-destructive",
  unknown: "bg-muted-foreground/60",
};

export function StatusDot({ status, className }: { status: AppHealthStatus; className?: string }) {
  return <span aria-hidden className={cn("size-2 shrink-0 rounded-full", DOT[status], className)} />;
}

export const STATUS_TEXT: Record<AppHealthStatus, string> = {
  healthy: "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
  degraded: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  down: "border-red-600/40 bg-red-600/15 text-red-700 dark:text-red-400",
  unknown: "text-muted-foreground",
};

/** A known reason code reads as words; anything else is shown as the backend sent it. */
export function useReasonLabel() {
  const t = useTranslations("appOverview.reasons");
  return (reason: string | null | undefined) =>
    reason ? (t.has(reason) ? t(reason) : reason) : null;
}

export function useDuration() {
  const t = useTranslations("appOverview.units");
  const units: DurationUnits = { s: t("s"), m: t("m"), h: t("h"), d: t("d") };
  return (seconds: number) => compactDuration(seconds, units);
}

const MIB = 2 ** 20;
const GIB = 2 ** 30;

export function bytesLabel(bytes: number): string {
  return bytes >= GIB ? `${(bytes / GIB).toFixed(bytes >= 10 * GIB ? 0 : 2)} Gi` : `${Math.round(bytes / MIB)} Mi`;
}
