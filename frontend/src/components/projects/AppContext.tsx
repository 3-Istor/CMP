"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { getDeployment } from "@/lib/api";
import { useDeploymentPolling } from "@/lib/hooks";
import type { Deployment } from "@/types";
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
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [loading, setLoading] = useState(true);

  const isActive = deployment ? ACTIVE_STATUSES.has(deployment.status) : false;
  const polled = useDeploymentPolling(isActive ? appId : null);
  const current = polled ?? deployment;

  const fetchDeployment = useCallback(async () => {
    try {
      const d = await getDeployment(appId);
      setDeployment(d);
    } catch {
      toast.error("Failed to load application");
    } finally {
      setLoading(false);
    }
  }, [appId]);

  useEffect(() => {
    fetchDeployment();
  }, [fetchDeployment]);

  // The deployment row is kept (status=deleted), not removed, once
  // teardown finishes, polling correctly stops, but nothing else takes the
  // user off a page for an app that no longer exists. Bounce back to the
  // project after a beat. Watches `current`: after a delete started here,
  // the final "deleted" status only arrives through polling.
  useEffect(() => {
    if (current?.status !== "deleted") return;
    toast.info(`"${current.name}" has been deleted`);
    const timer = setTimeout(
      () => router.push(`/projects/${projectName}`),
      2500,
    );
    return () => clearTimeout(timer);
  }, [current?.status, current?.name, projectName, router]);

  const markDeleting = useCallback(() => {
    setDeployment((prev) =>
      prev
        ? { ...prev, status: "deleting", step_message: "Destroying resources…" }
        : prev,
    );
  }, []);

  const value = useMemo(
    () =>
      current
        ? {
            appId,
            projectName,
            deployment: current,
            refresh: fetchDeployment,
            markDeleting,
          }
        : null,
    [appId, projectName, current, fetchDeployment, markDeleting],
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
