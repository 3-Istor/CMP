"use client";

import { deleteDeployment } from "@/lib/api";
import { useDeploymentsList } from "@/lib/hooks";
import { toast } from "sonner";
import { DeploymentCard } from "./DeploymentCard";

export function Dashboard() {
  const { deployments, loading, refresh } = useDeploymentsList(3000); // Keep 3s refresh for deployments

  const handleDelete = async (id: number) => {
    try {
      await deleteDeployment(id);
      toast.success("Deletion started");
      refresh();
    } catch (err) {
      toast.error(`Failed to delete: ${err}`);
    }
  };

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-48 rounded-lg bg-muted animate-pulse" />
        ))}
      </div>
    );
  }

  if (deployments.length === 0) {
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
      {deployments.map((d) => (
        <DeploymentCard key={d.id} deployment={d} onDelete={handleDelete} />
      ))}
    </div>
  );
}
