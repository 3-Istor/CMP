"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { CreateProjectModal } from "@/components/projects/CreateProjectModal";
import { ProjectsGrid } from "@/components/projects/ProjectsGrid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useProjectsLive } from "@/lib/useProjectsLive";
import { FolderKanban, FolderPlus, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

export default function ProjectsPage() {
  const t = useTranslations("projects.home");
  const to = useTranslations("overview");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { projects, loading, refresh } = useProjectsLive();
  const shown = projects.filter((p) =>
    p.name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={t("subtitle")}
        icon={FolderKanban}
        actions={
          <>
            <div className="relative">
              <Search
                className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={to("searchProjects")}
                aria-label={to("searchProjects")}
                className="h-8 w-56 pl-8"
              />
            </div>
            <Button size="sm" onClick={() => setOpen(true)}>
              <FolderPlus aria-hidden />
              {t("new")}
            </Button>
          </>
        }
      />
      <PageBody>
        <ProjectsGrid
          projects={shown}
          loading={loading}
          onCreate={() => setOpen(true)}
          onRetried={refresh}
        />
      </PageBody>
      <CreateProjectModal
        open={open}
        onClose={() => setOpen(false)}
        onCreated={refresh}
      />
    </div>
  );
}
