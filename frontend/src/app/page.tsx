"use client";

import { AssistantCard } from "@/components/agents/AssistantCard";
import { AppsTable } from "@/components/dashboard/AppsTable";
import { HomeStats } from "@/components/dashboard/HomeStats";
import { InfraPanel } from "@/components/dashboard/InfraPanel";
import { FavoritesSection } from "@/components/favorites/FavoritesSection";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { CreateProjectModal } from "@/components/projects/CreateProjectModal";
import { ProjectsGrid } from "@/components/projects/ProjectsGrid";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useDeploymentsList } from "@/lib/hooks";
import { useProjectsLive } from "@/lib/useProjectsLive";
import { ArrowRight, FolderPlus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { useEffect, useState } from "react";

const HOME_APPS = 6;

function SectionTitle({
  title,
  href,
  linkLabel,
}: {
  title: string;
  href?: string;
  linkLabel?: string;
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-base font-semibold">{title}</h2>
      {href && (
        <Link
          href={href}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          {linkLabel}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </Link>
      )}
    </div>
  );
}

export default function Home() {
  const { data: session } = useSession();
  const t = useTranslations("shell.home");
  const to = useTranslations("overview");
  const tProjects = useTranslations("projects.home");
  const locale = useLocale();
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const { projects, loading: loadingProjects, refresh: refreshProjects } =
    useProjectsLive();
  const { deployments, loading: loadingApps } = useDeploymentsList(5000, 30000);
  const apps = deployments
    .filter((d) => d.status !== "deleted" && d.project_id !== null)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  const firstName =
    session?.user?.given_name ||
    session?.user?.name?.split(" ")[0] ||
    session?.user?.email?.split("@")[0] ||
    t("fallbackName");
  // Computed in the browser: the server runs in UTC and would print another day near midnight.
  const [today, setToday] = useState("");
  useEffect(() => {
    setToday(
      new Date().toLocaleDateString(locale, {
        weekday: "long",
        day: "numeric",
        month: "long",
      }),
    );
  }, [locale]);

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("greeting", { name: firstName })}
        description={today ? <span className="first-letter:uppercase">{today}</span> : undefined}
        actions={
          <Button size="sm" onClick={() => setCreateProjectOpen(true)}>
            <FolderPlus aria-hidden />
            {tProjects("new")}
          </Button>
        }
      />
      <PageBody className="space-y-8">
        <HomeStats apps={apps} loading={loadingApps} />

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section>
            <SectionTitle
              title={to("recentApps")}
              href="/apps"
              linkLabel={to("allApps", { count: apps.length })}
            />
            <Card className="py-0">
              <AppsTable apps={apps} loading={loadingApps} limit={HOME_APPS} />
            </Card>
          </section>
          <aside className="space-y-6">
            <AssistantCard />
            <InfraPanel />
          </aside>
        </div>

        <FavoritesSection onRetried={refreshProjects} />

        <section>
          <SectionTitle
            title={tProjects("title")}
            href="/projects"
            linkLabel={to("allProjects")}
          />
          <ProjectsGrid
            projects={projects}
            loading={loadingProjects}
            onCreate={() => setCreateProjectOpen(true)}
            onRetried={refreshProjects}
          />
        </section>
      </PageBody>

      <CreateProjectModal
        open={createProjectOpen}
        onClose={() => setCreateProjectOpen(false)}
        onCreated={refreshProjects}
      />
    </div>
  );
}
