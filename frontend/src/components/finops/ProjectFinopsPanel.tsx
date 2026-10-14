"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { useFinopsOverview } from "@/lib/hooks";
import { AlertCircle } from "lucide-react";
import Link from "next/link";
import { KpiCards } from "./KpiCards";

/** The FinOps summary of one project, for the project page's FinOps tab. */
export function ProjectFinopsPanel({ project }: { project: string }) {
  const { data, loading, error } = useFinopsOverview(project);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Link
          href={`/finops?project=${encodeURIComponent(project)}`}
          className="text-xs text-primary hover:underline"
        >
          Ouvrir dans FinOps →
        </Link>
      </div>
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}
      {loading && !data ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-40 rounded-xl" />
          ))}
        </div>
      ) : (
        data && (
          <KpiCards summary={data.summary} budget={data.budget} project={project} />
        )
      )}
    </div>
  );
}
