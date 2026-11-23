"use client";

import { getPlatformPlacement } from "@/lib/api";
import { useGlobalHealth } from "@/lib/hooks";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { useEffect, useMemo, useState } from "react";

/** Memory % requested per cloud; platform admins only, empty when the placement API is unavailable. */
export function usePlacementMemory() {
  const isAdmin = usePlatformAdmin();
  const [memory, setMemory] = useState<Record<string, number | null>>({});
  useEffect(() => {
    if (!isAdmin) return;
    getPlatformPlacement()
      .then((d) => setMemory(Object.fromEntries(d.clusters.map((c) => [c.cloud, c.memory_requested_pct]))))
      .catch(() => undefined);
  }, [isAdmin]);
  return memory;
}

/** State per cloud (null until known; on-prem is not in the health list, so it counts as up) and memory %. */
export function useClusterStates() {
  const { health } = useGlobalHealth(0);
  const memory = usePlacementMemory();
  const states = useMemo<Record<string, string> | null>(
    () => (health ? { onprem: "up", ...Object.fromEntries(health.clusters.map((c) => [c.cloud, c.state])) } : null),
    [health],
  );
  return { states, memory };
}
