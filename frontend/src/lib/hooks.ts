"use client";

import type { Deployment } from "@/types";
import { useCallback, useEffect, useRef, useState } from "react";
import { getDeployment, getDeployments } from "./api";

const TERMINAL_STATUSES = new Set(["running", "failed", "deleted"]);

/** Poll a single deployment until it reaches a terminal state. */
export function useDeploymentPolling(
  id: number | null,
  intervalMs = 3000,
): Deployment | null {
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  useEffect(() => {
    if (!id) return;

    const poll = async () => {
      try {
        const d = await getDeployment(id);
        setDeployment(d);
        if (TERMINAL_STATUSES.has(d.status)) stop();
      } catch {
        stop();
      }
    };

    poll();
    timerRef.current = setInterval(poll, intervalMs);
    return stop;
  }, [id, intervalMs, stop]);

  return deployment;
}

/** Poll the full deployments list at a fixed interval. */
export function useDeploymentsList(intervalMs = 5000) {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getDeployments();
      setDeployments(data);
    } catch {
      // Backend offline — silently keep existing state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // Don't poll if interval is 0 or negative (manual refresh only)
    if (intervalMs <= 0) return;
    const timer = setInterval(refresh, intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs]);

  return { deployments, loading, refresh };
}
