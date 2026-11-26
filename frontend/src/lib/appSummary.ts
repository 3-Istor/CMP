"use client";

import { cachedGet, getAppSummary, type AppEnv, type SummaryRange } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import type { ReleaseEvent } from "@/types";
import { useEffect, useRef, useState } from "react";

// The frame header, the overview and a hover on the project list ask for the same summary: one answer serves all three.
const SHARED_MS = 25_000;

export function useAppSummary(appId: number, range: SummaryRange, enabled: boolean, env: AppEnv = "prod") {
  return useCardData(
    `summary-${appId}-${range}-${env}`,
    () => getAppSummary(appId, range, SHARED_MS, env),
    { enabled, intervalMs: 30_000 },
  );
}

export function prefetchAppSummary(appId: number) {
  getAppSummary(appId, "24h", SHARED_MS).catch(() => undefined);
}

export function useProdReleases(appId: number, enabled: boolean) {
  return useCardData(
    `prod-releases-${appId}`,
    () => cachedGet<ReleaseEvent[]>(`/deployments/${appId}/releases?environment=prod`, SHARED_MS),
    { enabled },
  );
}

/** True once the element came near the viewport: below-the-fold cards fetch only then. */
export function useInView<T extends Element>(rootMargin = "200px") {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen) return;
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [seen, rootMargin]);
  return [ref, seen] as const;
}
