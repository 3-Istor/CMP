"use client";

import type { FinopsMeta } from "@/types";
import { formatAge } from "./format";

const PILL = "inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px]";

function pick(
  values: (string | null)[],
  better: (a: string, b: string) => boolean,
): string | null {
  const present = values.filter((v): v is string => v !== null);
  return present.length
    ? present.reduce((a, b) => (better(a, b) ? a : b))
    : null;
}

export function DataFreshness({
  meta,
  compact = false,
}: {
  meta: FinopsMeta | null;
  /** Nature badge only, for data that has no cost-line freshness. */
  compact?: boolean;
}) {
  if (!meta) return null;
  const real = compact || meta.provider === "real";
  const detailed = real && !compact;
  const ingested = pick(
    meta.sources.map((s) => s.last_ingested_at),
    (a, b) => a > b,
  );
  const finalThrough = pick(
    meta.sources.map((s) => s.final_through),
    (a, b) => a < b,
  );

  return (
    <div className="flex flex-wrap items-center gap-1">
      <span
        className={`${PILL} ${
          real
            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
            : "bg-amber-500/15 text-amber-600 dark:text-amber-400"
        }`}
      >
        {real ? "réel" : "simulé"}
      </span>
      {detailed && meta.sources.some((s) => s.has_estimate) && (
        <span
          className={`${PILL} bg-amber-500/15 text-amber-600 dark:text-amber-400`}
          title="Une partie des coûts repose sur un prix provisoire."
        >
          estimé
        </span>
      )}
      {detailed && (
        <span className={`${PILL} bg-muted text-muted-foreground`}>
          màj {formatAge(ingested)}
        </span>
      )}
      {detailed && finalThrough && (
        <span className={`${PILL} bg-muted text-muted-foreground`}>
          définitif jusqu&apos;au{" "}
          {new Date(finalThrough).toLocaleDateString("fr-FR")}
        </span>
      )}
    </div>
  );
}
