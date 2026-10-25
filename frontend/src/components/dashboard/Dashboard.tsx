"use client";

import { deleteDeployment } from "@/lib/api";
import { useDeploymentsList, useProjects } from "@/lib/hooks";
import { can } from "@/lib/permissions";
import type { ProjectRole } from "@/types";
import { useMemo } from "react";
import { toast } from "sonner";
import { DeploymentCard } from "./DeploymentCard";

export function Dashboard() {
  // Fast (3s) only while a deployment is in progress; idle every 10s otherwise.
  const { deployments, loading, refresh } = useDeploymentsList(3000, 10000);
  const { projects, loading: loadingProjects } = useProjects();

  // `GET /deployments/` is scoped to the caller's projects server-side, so
  // this map is really about the per-card delete permission. The filter below
  // is belt-and-braces for a stale response.
  const roleByProject = useMemo(() => {
    const map = new Map<string, ProjectRole>();
    for (const p of projects) map.set(p.name, p.role);
    return map;
  }, [projects]);

  const visible = useMemo(
    () =>
      deployments.filter(
        (d) => d.project_id !== null && roleByProject.has(d.project_id),
      ),
    [deployments, roleByProject],
  );

  const handleDelete = async (id: number) => {
    const deployment = visible.find((d) => d.id === id);
    const role = deployment?.project_id
      ? roleByProject.get(deployment.project_id)
      : undefined;
    if (!can(role, "app.delete")) {
      toast.error("Only project admins can delete an application");
      return;
    }
    try {
      await deleteDeployment(id);
      toast.success("Deletion started");
      refresh();
    } catch (err) {
      toast.error(`Failed to delete: ${err}`);
    }
  };

  if (loading || loadingProjects) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-48 rounded-lg bg-muted animate-pulse" />
        ))}
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <span className="text-5xl mb-4">🌩️</span>
        <p className="text-lg font-medium">No deployments yet</p>
        <p className="text-sm">
          Deploy an app from the catalog to get started.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {visible.map((d) => (
        <DeploymentCard
          key={d.id}
          deployment={d}
          onDelete={handleDelete}
          canDelete={can(
            d.project_id ? roleByProject.get(d.project_id) : undefined,
            "app.delete",
          )}
        />
      ))}
    </div>
  );
}
