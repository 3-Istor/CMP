"use client";

import type { Deployment, Project } from "@/types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    getDeployment,
    getDeployments,
    getProjectApps,
    getProjects,
} from "./api";
import { can, type Capability } from "./permissions";

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

// ── Projects cache ──────────────────────────────────────────────────────────
// The project list is now read for permissions as well as for display, so the
// sidebar, the project page and the app page all want it at once. Each
// getProjects() costs the backend a Keycloak Admin round-trip, so hold the
// result briefly and share it. Same shape as the token cache in api.ts.
let _projectsCache: { data: Project[]; at: number } | null = null;
let _projectsInflight: Promise<Project[]> | null = null;
const PROJECTS_TTL_MS = 15_000;

async function loadProjects(force = false): Promise<Project[]> {
  const now = Date.now();
  if (!force && _projectsCache && now - _projectsCache.at < PROJECTS_TTL_MS) {
    return _projectsCache.data;
  }
  if (_projectsInflight) return _projectsInflight;

  _projectsInflight = (async () => {
    try {
      const data = await getProjects();
      _projectsCache = { data, at: Date.now() };
      return data;
    } finally {
      _projectsInflight = null;
    }
  })();
  return _projectsInflight;
}

/**
 * Drop the cached project list.
 *
 * Call after creating or deleting a project, or after changing a member's
 * role, so the next read reflects it instead of waiting out the TTL.
 */
export function clearProjectsCache() {
  _projectsCache = null;
}

/**
 * Fetch the list of projects the current user belongs to.
 *
 * @param intervalMs when > 0, re-fetch on this cadence so the list stays in
 *   sync with creations/deletions made elsewhere (e.g. the persistent sidebar).
 *   Defaults to 0 (fetch once on mount).
 */
export function useProjects(intervalMs = 0) {
  const [projects, setProjects] = useState<Project[]>(
    () => _projectsCache?.data ?? [],
  );
  const [loading, setLoading] = useState(!_projectsCache);
  const [error, setError] = useState<string | null>(null);
  const initialLoadDone = useRef(Boolean(_projectsCache));

  const load = useCallback(async (force: boolean) => {
    // Only show full skeleton on first load; subsequent refreshes update silently
    if (!initialLoadDone.current) setLoading(true);
    try {
      const data = await loadProjects(force);
      setProjects(data);
      setError(null);
      initialLoadDone.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch projects");
    } finally {
      setLoading(false);
    }
  }, []);

  // Bypasses the cache: callers reach for refresh() precisely because they
  // just changed something.
  const refresh = useCallback(() => load(true), [load]);

  useEffect(() => {
    load(false);
    if (intervalMs <= 0) return;
    const timer = setInterval(() => load(true), intervalMs);
    return () => clearInterval(timer);
  }, [load, intervalMs]);

  return { projects, loading, error, refresh };
}

/**
 * The current user's role on one project, or null if they have none.
 *
 * Reads the project list rather than a dedicated endpoint: `GET /projects/`
 * already returns the role, and the list is cached, so this is free for any
 * screen that already renders it.
 */
export function useProjectRole(projectName: string | null) {
  const { projects, loading, error, refresh } = useProjects();

  const role = useMemo(
    () => projects.find((p) => p.name === projectName)?.role ?? null,
    [projects, projectName],
  );

  return { role, loading, error, refresh };
}

/**
 * Capability check for one project.
 *
 * While `loading`, `role` is null and every `can()` is false. Render action
 * buttons as absent during that window rather than optimistically: they
 * appear a beat later, which is better than flashing a control the user is
 * not allowed to use.
 */
export function useCan(projectName: string | null) {
  const { role, loading, error, refresh } = useProjectRole(projectName);

  const check = useCallback(
    (capability: Capability) => can(role, capability),
    [role],
  );

  return { role, loading, error, refresh, can: check };
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
  cloud?: string,
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
      const res = await getFinopsOverview({
        project,
        period,
        granularity,
        cloud,
      });
      setData(res);
      setError(null);
      initialLoadDone.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch FinOps data");
    } finally {
      setLoading(false);
    }
  }, [project, period, granularity, cloud]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}

/** Savings ledger (évité / encaissé), re-fetched when the filters change. */
export function useFinopsSavings(project?: string, period = "ytd") {
  const [data, setData] = useState<import("@/types").FinopsSavings | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("./api")
      .then(({ getFinopsSavings }) => getFinopsSavings({ project, period }))
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : "Failed to fetch savings",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project, period]);

  return { data, loading, error };
}

/** Energy and carbon of on-prem workloads, re-fetched when the filters change. */
export function useFinopsCarbon(project?: string, period = "30d") {
  const [data, setData] = useState<import("@/types").FinopsCarbon | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("./api")
      .then(({ getFinopsCarbon }) => getFinopsCarbon({ project, period }))
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : "Failed to fetch carbon",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project, period]);

  return { data, loading, error };
}

/** Nature (simulé / réel) and freshness of the FinOps data. */
export function useFinopsMeta() {
  const [meta, setMeta] = useState<import("@/types").FinopsMeta | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("./api")
      .then(({ getFinopsMeta }) => getFinopsMeta())
      .then((res) => {
        if (!cancelled) setMeta(res);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return meta;
}

/** Closed months and cost centers of the Finance tab, with their refresh. */
export function useFinopsFinance() {
  const [closes, setCloses] = useState<import("@/types").FinopsMonthClose[]>([]);
  const [centers, setCenters] = useState<import("@/types").FinopsCostCenter[]>(
    [],
  );
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { getFinopsCloses, getFinopsCostCenters } = await import("./api");
      const [c, cc] = await Promise.all([
        getFinopsCloses(),
        getFinopsCostCenters(),
      ]);
      setCloses(c);
      setCenters(cc);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chargement impossible");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { closes, centers, error, refresh };
}

/** Platform cost report; the endpoint refuses non admin/finance callers. */
export function useFinopsPlatform(period: string, enabled: boolean) {
  const [data, setData] = useState<import("@/types").PlatformReport | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    import("./api")
      .then(({ getFinopsPlatform }) => getFinopsPlatform(period))
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Chargement impossible");
      });
    return () => {
      cancelled = true;
    };
  }, [period, enabled]);

  return { data: enabled ? data : null, error };
}

/** Daily cost anomalies per app; skipped when disabled (non-daily view). */
export function useFinopsAnomalies(
  project?: string,
  period = "30d",
  enabled = true,
) {
  const [data, setData] = useState<import("@/types").CostAnomaly[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    import("./api")
      .then(({ getFinopsAnomalies }) => getFinopsAnomalies({ project, period }))
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setData([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project, period, enabled]);

  return enabled ? data : [];
}

/** Fetch members of a specific project. */
export function useProjectMembers(projectName: string | null) {
  const [members, setMembers] = useState<import("@/types").ProjectMember[]>([]);
  // Assume guests are supported until told otherwise: the flag only ever
  // removes an option, and a stale `false` would hide Guest on a project that
  // does have the group.
  const [guestsSupported, setGuestsSupported] = useState(true);
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
      setGuestsSupported(data.guests_supported !== false);
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

  return { members, guestsSupported, loading, error, refresh };
}

/** Security grade of every visible project, for badges. Polls slowly. */
export function useSecurityOverview(intervalMs = 120000) {
  const [overview, setOverview] = useState<
    import("@/types").SecurityProjectOverview[]
  >([]);

  const refresh = useCallback(async () => {
    try {
      const { getSecurityOverview } = await import("./api");
      setOverview(await getSecurityOverview());
    } catch {
      // Badges are a convenience: the security page reports its own errors.
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs]);

  return overview;
}

const RELEASE_POLL_MS = 3000;
const RELEASE_RECONNECT_MS = 2000;
const RELEASE_STREAM_ATTEMPTS = 2;

/**
 * Live release state of one deployment.
 *
 * Reads the SSE stream; if it cannot be opened twice in a row (proxy buffering,
 * older backend), falls back to polling every 3s for the life of the hook.
 */
export function useRelease(deploymentId: number) {
  const [view, setView] = useState<import("@/types").ReleaseView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<"stream" | "polling" | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { getRelease } = await import("./api");
      setView(await getRelease(deploymentId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch release");
    }
  }, [deploymentId]);

  useEffect(() => {
    const controller = new AbortController();
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;

    const startPolling = () => {
      setLive("polling");
      pollTimer = setInterval(refresh, RELEASE_POLL_MS);
    };

    const connect = async () => {
      const { streamRelease } = await import("./api");
      let received = false;
      try {
        await streamRelease(
          deploymentId,
          (v) => {
            received = true;
            failures = 0;
            setLive("stream");
            setView(v);
            setError(null);
          },
          controller.signal,
        );
      } catch {
        if (controller.signal.aborted) return;
        if (!received) failures++;
      }
      if (controller.signal.aborted) return;
      if (failures >= RELEASE_STREAM_ATTEMPTS) {
        startPolling();
        return;
      }
      reconnectTimer = setTimeout(connect, RELEASE_RECONNECT_MS);
    };

    refresh();
    connect();
    return () => {
      controller.abort();
      if (pollTimer) clearInterval(pollTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [deploymentId, refresh]);

  return { view, error, live, refresh };
}

/** Past and current releases of one deployment, newest first. */
export function useReleaseHistory(deploymentId: number) {
  const [events, setEvents] = useState<import("@/types").ReleaseEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { getReleaseHistory } = await import("./api");
      setEvents(await getReleaseHistory(deploymentId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch history");
    } finally {
      setLoading(false);
    }
  }, [deploymentId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { events, loading, error, refresh };
}
