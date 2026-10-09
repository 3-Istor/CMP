"use client";

import { SecurityDashboard } from "@/components/security/SecurityDashboard";
import { getSecurityOverview, getSecurityRole } from "@/lib/api";
import { useProjectApps, useProjects } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { SecurityProjectOverview, SecurityView } from "@/types";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

const SELECT =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

function SecurityInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { projects } = useProjects();
  const [overview, setOverview] = useState<SecurityProjectOverview[]>([]);
  const [isCnpAdmin, setIsCnpAdmin] = useState(false);

  const project = searchParams.get("project") ?? projects[0]?.name ?? "";
  const app = searchParams.get("app");
  const view = (searchParams.get("view") as SecurityView) ?? "developer";
  const { apps } = useProjectApps(project || null);

  useEffect(() => {
    if (!project) return;
    getSecurityRole(project)
      .then((r) => setIsCnpAdmin(r.cnp_admin))
      .catch(() => setIsCnpAdmin(false));
  }, [project]);

  useEffect(() => {
    getSecurityOverview()
      .then(setOverview)
      .catch(() => setOverview([]));
  }, []);

  function navigate(next: { project?: string; app?: string | null; view?: SecurityView }) {
    const params = new URLSearchParams();
    const nextProject = next.project ?? project;
    if (nextProject) params.set("project", nextProject);
    const nextApp = next.project !== undefined ? next.app ?? null : next.app ?? app;
    if (nextApp) params.set("app", nextApp);
    const nextView = next.view ?? view;
    if (nextView === "platform") params.set("view", nextView);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  const grades = new Map(overview.map((o) => [o.project, o]));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Projet"
          className={SELECT}
          value={project}
          onChange={(e) => navigate({ project: e.target.value, app: null })}
        >
          {projects.map((p) => {
            const o = grades.get(p.name);
            return (
              <option key={p.name} value={p.name}>
                {p.name}
                {o ? ` · ${o.grade}${o.core ? ` · ${o.core} obligatoire${o.core > 1 ? "s" : ""}` : ""}` : ""}
              </option>
            );
          })}
        </select>
        <select
          aria-label="Application"
          className={SELECT}
          value={app ?? ""}
          onChange={(e) => navigate({ app: e.target.value || null })}
        >
          <option value="">Tout le projet</option>
          {apps.map((a) => (
            <option key={a.id} value={a.name}>
              {a.name}
            </option>
          ))}
        </select>
        {isCnpAdmin && (
        <div className="ml-auto flex rounded-lg border p-0.5 text-sm">
          {(["developer", "platform"] as SecurityView[]).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => navigate({ view: v })}
              className={cn(
                "rounded-md px-2.5 py-1",
                view === v
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {v === "developer" ? "Développeur" : "Plateforme"}
            </button>
          ))}
        </div>
        )}
      </div>

      {project ? (
        <SecurityDashboard key={`${project}/${app}/${view}`} project={project} app={app} view={view} />
      ) : (
        <p className="text-sm text-muted-foreground">Aucun projet.</p>
      )}
    </div>
  );
}

export default function SecurityPage() {
  return (
    <Suspense>
      <SecurityInner />
    </Suspense>
  );
}
