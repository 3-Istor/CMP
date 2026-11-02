"use client";

import { useVisibleWork } from "@/components/layout/shellData";
import { AppCard } from "@/components/projects/AppCard";
import { ProjectCard } from "@/components/projects/ProjectCard";
import { useFavorites } from "@/lib/favorites";
import { useTranslations } from "next-intl";

export function FavoritesSection({ onRetried }: { onRetried: () => void }) {
  const t = useTranslations("favorites");
  const favorites = useFavorites();
  const { projects, apps } = useVisibleWork(0);

  const favProjects = projects.filter((p) =>
    favorites.projects.includes(p.name),
  );
  const favApps = apps.flatMap((a) =>
    a.project_id && favorites.apps.includes(a.id)
      ? [{ app: a, projectName: a.project_id }]
      : [],
  );

  if (favProjects.length + favApps.length === 0) return null;

  return (
    <section id="favorites" className="scroll-mt-6">
      <h2 className="mb-4 text-xl font-semibold">{t("title")}</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {favProjects.map((p) => (
          <ProjectCard
            key={`project-${p.name}`}
            project={p}
            onRetried={onRetried}
          />
        ))}
        {favApps.map(({ app, projectName }) => (
          <AppCard key={`app-${app.id}`} app={app} projectName={projectName} />
        ))}
      </div>
    </section>
  );
}
