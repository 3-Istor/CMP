"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { cachedAnswer, getDeployment, projectAppsPath } from "@/lib/api";
import { APPS_REFRESH_MS } from "@/components/layout/ProjectNav";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { pollDue } from "@/lib/hooks";
import type { Deployment } from "@/types";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { toast } from "sonner";

const ACTIVE_STATUSES = new Set([
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
]);
const SLOW_POLL_STATUSES = new Set(["failed", "degraded"]);

interface AppContextValue {
  appId: number;
  projectName: string;
  deployment: Deployment;
  refresh: () => Promise<void>;
  markDeleting: () => void;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useAppContext(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useAppContext outside AppProvider");
  return value;
}

export function AppProvider({
  projectName,
  appId,
  children,
}: {
  projectName: string;
  appId: number;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const t = useTranslations("apps.context");
  const { refreshApps } = useProjectContext();
  // A list row the sidebar fetched within its own poll shows the page at once;
  // getDeployment still refreshes it.
  const [deployment, setDeployment] = useState<Deployment | null>(
    () =>
      cachedAnswer<Deployment[]>(
        projectAppsPath(projectName),
        APPS_REFRESH_MS,
      )?.find((a) => a.id === appId) ?? null,
  );
  const [loading, setLoading] = useState(!deployment);

  // Fast while a run is in progress (the old polling default); slow on failed
  // and degraded, which the backend health poller moves once a minute.
  const pollMs = !deployment
    ? 0
    : ACTIVE_STATUSES.has(deployment.status)
      ? 3000
      : SLOW_POLL_STATUSES.has(deployment.status)
        ? 60_000
        : 0;
  useEffect(() => {
    if (!pollMs) return;
    // Reuses an answer younger than pollMs; a failed poll keeps the last one.
    const read = () =>
      getDeployment(appId, pollMs).then(
        (d) => {
          setDeployment(d);
          return d;
        },
        () => undefined,
      );
    return pollDue(read, pollMs);
  }, [appId, pollMs]);

  const fetchDeployment = useCallback(async () => {
    try {
      const d = await getDeployment(appId);
      setDeployment(d);
    } catch {
      toast.error(t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [appId, t]);

  useEffect(() => {
    fetchDeployment();
  }, [fetchDeployment]);

  // The deployment row is kept (status=deleted), not removed, once
  // teardown finishes, polling correctly stops, but nothing else takes the
  // user off a page for an app that no longer exists. Bounce back to the
  // project after a beat. Watches `deployment`: after a delete started here,
  // the final "deleted" status only arrives through polling.
  useEffect(() => {
    if (deployment?.status !== "deleted") return;
    toast.info(t("deleted", { name: deployment.name }));
    const timer = setTimeout(
      () => router.push(`/projects/${projectName}`),
      2500,
    );
    return () => clearTimeout(timer);
  }, [deployment?.status, deployment?.name, projectName, router, t]);

  // A GET that raced the backend's DELETING commit may leave a stale list row.
  const status = deployment?.status;
  useEffect(() => {
    if (status === "deleting" || status === "deleted") void refreshApps();
  }, [status, refreshApps]);

  const markDeleting = useCallback(() => {
    setDeployment((prev) =>
      prev
        ? { ...prev, status: "deleting", step_message: t("destroying") }
        : prev,
    );
    // Lists read the project's apps: refresh them so no stale "Running" stays.
    void refreshApps();
  }, [t, refreshApps]);

  const value = useMemo(
    () =>
      deployment
        ? {
            appId,
            projectName,
            deployment,
            refresh: fetchDeployment,
            markDeleting,
          }
        : null,
    [appId, projectName, deployment, fetchDeployment, markDeleting],
  );

  if (loading || !value) {
    return (
      <div className="min-h-screen">
        <main className="px-6 py-8 max-w-5xl mx-auto space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-24 w-full" />
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-4">
              <Skeleton className="h-48" />
              <Skeleton className="h-64" />
            </div>
            <Skeleton className="h-80" />
          </div>
        </main>
      </div>
    );
  }

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
