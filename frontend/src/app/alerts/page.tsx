"use client";

import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import { useProjects } from "@/lib/hooks";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

const SELECT =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

function AlertsInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { projects } = useProjects();
  const selected = searchParams.get("project") ?? projects[0]?.name ?? "";

  return (
    <main className="mx-auto max-w-7xl space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Alertes</h1>
          <p className="text-sm text-muted-foreground">
            Les alertes prêtes à l&apos;emploi de chaque projet.
          </p>
        </div>
        <select
          aria-label="Projet"
          className={SELECT}
          value={selected}
          onChange={(e) =>
            router.replace(
              `${pathname}?project=${encodeURIComponent(e.target.value)}`,
            )
          }
        >
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      {selected ? (
        <AlertsPanel key={selected} project={selected} />
      ) : (
        <p className="text-sm text-muted-foreground">Aucun projet.</p>
      )}
    </main>
  );
}

export default function AlertsPage() {
  return (
    <Suspense>
      <AlertsInner />
    </Suspense>
  );
}
