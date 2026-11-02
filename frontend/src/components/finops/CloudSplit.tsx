"use client";

import type { FinopsCloud } from "@/types";
import { CLOUDS, CLOUD_META, formatEUR } from "./format";

export function CloudSplit({
  byCloud,
  currency,
}: {
  byCloud: Record<FinopsCloud, number>;
  currency: string;
}) {
  const total = CLOUDS.reduce((s, c) => s + (byCloud[c] ?? 0), 0);

  return (
    <div className="space-y-3">
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted">
        {CLOUDS.map((c) => (
          <div
            key={c}
            style={{
              width: `${total > 0 ? ((byCloud[c] ?? 0) / total) * 100 : 0}%`,
              backgroundColor: CLOUD_META[c].color,
            }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {CLOUDS.map((c) => (
          <span key={c} className="flex items-center gap-1.5">
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: CLOUD_META[c].color }}
            />
            {CLOUD_META[c].label}
            <span className="tabular-nums text-muted-foreground">
              {formatEUR(byCloud[c] ?? 0, currency)}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
