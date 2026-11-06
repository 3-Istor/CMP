"use client";

import { AppsTable } from "@/components/dashboard/AppsTable";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useDeploymentsList } from "@/lib/hooks";
import { Boxes, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

const SELECT =
  "h-8 rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

export default function AppsPage() {
  const t = useTranslations("overview");
  const { deployments, loading } = useDeploymentsList(5000, 30000);
  const [query, setQuery] = useState("");
  const [project, setProject] = useState("");
  const apps = deployments.filter((d) => d.status !== "deleted" && d.project_id !== null);
  const projects = [...new Set(apps.map((a) => a.project_id).filter(Boolean))] as string[];
  const q = query.trim().toLowerCase();
  const shown = apps
    .filter((a) => !project || a.project_id === project)
    .filter((a) => !q || a.name.toLowerCase().includes(q))
    .sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("appsTitle")}
        description={t("appsSubtitle")}
        icon={Boxes}
        actions={
          <>
            <select
              aria-label={t("apps.project")}
              className={SELECT}
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">{t("allProjectsOption")}</option>
              {projects.sort().map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <div className="relative">
              <Search
                className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("searchApps")}
                aria-label={t("searchApps")}
                className="h-8 w-56 pl-8"
              />
            </div>
          </>
        }
      />
      <PageBody>
        <Card className="py-0">
          <AppsTable apps={shown} loading={loading} />
        </Card>
      </PageBody>
    </div>
  );
}
