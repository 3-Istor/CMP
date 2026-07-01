"use client";

import type { Deployment, Project } from "@/types";
import { useCallback, useEffect, useRef, useState } from "react";
import {
    getDeployment,
    getDeployments,
    getProjectApps,
    getProjects,
} from "./api";

const TERMINAL_STATUSES = new Set(["running", "failed", "deleted"]);

/** Poll a single deployment until it reaches a terminal state. */
export function useDeploymentPolling(
  id: number | null,
  intervalMs = 3000,
): Deployment | null {
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const terminalReachedRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  useEffect(() => {
    if (!id) return;

    const poll = async () => {
      try {
        const d = await getDeployment(id);
        setDeployment(d);

        // When reaching terminal status, continue polling for 5 more cycles
        // to ensure UI updates with final state (outputs, health, etc.)
        if (TERMINAL_STATUSES.has(d.status)) {
          if (terminalReachedRef.current === null) {
            terminalReachedRef.current = 5;
          } else {
            terminalReachedRef.current--;
            if (terminalReachedRef.current <= 0) {
              stop();
            }
          }
        }
      } catch {
        stop();
      }
    };

    poll();
    timerRef.current = setInterval(poll, intervalMs);
    return () => {
      stop();
      terminalReachedRef.current = null;
    };
  }, [id, intervalMs, stop]);

  return deployment;
}

const ACTIVE_DEPLOYMENT_STATUSES = new Set([
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
]);

/**
 * Poll the full deployments list, adapting the cadence to activity: fast while
 * a deployment is in progress, slow (or paused) when everything is idle.
 *
 * @param activeMs interval used while at least one deployment is transitioning
 * @param idleMs   interval used when all deployments are in a terminal state
 *                 (defaults to `activeMs` for backwards compatibility)
 */
export function useDeploymentsList(activeMs = 5000, idleMs = activeMs) {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getDeployments();
      setDeployments(data);
    } catch {
      // Backend offline - silently keep existing state
    } finally {
      setLoading(false);
    }
  }, []);

  const hasActive = deployments.some((d) =>
    ACTIVE_DEPLOYMENT_STATUSES.has(d.status),
  );
  const intervalMs = hasActive ? activeMs : idleMs;

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

/**
 * Fetch the list of projects the current user belongs to.
 *
 * @param intervalMs when > 0, re-fetch on this cadence so the list stays in
 *   sync with creations/deletions made elsewhere (e.g. the persistent sidebar).
 *   Defaults to 0 (fetch once on mount).
 */
export function useProjects(intervalMs = 0) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialLoadDone = useRef(false);

  const refresh = useCallback(async () => {
    // Only show full skeleton on first load; subsequent refreshes update silently
    if (!initialLoadDone.current) setLoading(true);
    try {
      const data = await getProjects();
      setProjects(data);
      setError(null);
      initialLoadDone.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch projects");
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

  return { projects, loading, error, refresh };
}

/** Fetch applications belonging to a specific project. */
export function useProjectApps(projectName: string | null) {
  const [apps, setApps] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialLoadDone = useRef(false);

  const refresh = useCallback(async () => {
    if (!projectName) return;
    if (!initialLoadDone.current) setLoading(true);
    try {
      const data = await getProjectApps(projectName);
      setApps(data);
      setError(null);
      initialLoadDone.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch apps");
    } finally {
      setLoading(false);
    }
  }, [projectName]);

  useEffect(() => {
    if (!projectName) {
      setLoading(false);
      return;
    }
    initialLoadDone.current = false; // reset on projectName change
    refresh();
  }, [refresh, projectName]);

  return { apps, loading, error, refresh };
}

/**
 * Fetch the FinOps overview (KPIs + budget + apps + timeline), re-fetching when
 * the project/period/granularity filters change.
 */
export function useFinopsOverview(
  project?: string,
  period = "30d",
  granularity = "daily",
) {
  const [data, setData] = useState<import("@/types").FinopsOverview | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialLoadDone = useRef(false);

  const refresh = useCallback(async () => {
    if (!initialLoadDone.current) setLoading(true);
    try {
      const { getFinopsOverview } = await import("./api");
      const res = await getFinopsOverview({ project, period, granularity });
      setData(res);
      setError(null);
      initialLoadDone.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch FinOps data");
    } finally {
      setLoading(false);
    }
  }, [project, period, granularity]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}

/** Fetch members of a specific project. */
export function useProjectMembers(projectName: string | null) {
  const [members, setMembers] = useState<import("@/types").ProjectMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialLoadDone = useRef(false);

  const refresh = useCallback(async () => {
    if (!projectName) return;
    if (!initialLoadDone.current) setLoading(true);
    try {
      const { getProjectMembers } = await import("./api");
      const data = await getProjectMembers(projectName);
      setMembers(data.members);
      setError(null);
      initialLoadDone.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch members");
    } finally {
      setLoading(false);
    }
  }, [projectName]);

  useEffect(() => {
    if (!projectName) {
      setLoading(false);
      return;
    }
    initialLoadDone.current = false;
    refresh();
  }, [refresh, projectName]);

  return { members, loading, error, refresh };
}
