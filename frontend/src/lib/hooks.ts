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

/** Poll global infrastructure health at a fixed interval. */
export function useGlobalHealth(intervalMs = 15000) {
  const [health, setHealth] = useState<
    import("@/types").GlobalHealthResponse | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { getGlobalHealth } = await import("./api");
      const data = await getGlobalHealth();
      setHealth(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch health");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (intervalMs <= 0) return;
    const timer = setInterval(refresh, intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs]);

  return { health, loading, error, refresh };
}

/** Poll application-specific health at a fixed interval. */
export function useAppHealth(deploymentId: number | null, intervalMs = 5000) {
  const [health, setHealth] = useState<
    import("@/types").AppHealthResponse | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!deploymentId) return;
    try {
      const { getAppHealth } = await import("./api");
      const data = await getAppHealth(deploymentId);
      setHealth(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch health");
    } finally {
      setLoading(false);
    }
  }, [deploymentId]);

  useEffect(() => {
    if (!deploymentId) {
      setLoading(false);
      return;
    }
    refresh();
    if (intervalMs <= 0) return;
    const timer = setInterval(refresh, intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs, deploymentId]);

  return { health, loading, error, refresh };
}
