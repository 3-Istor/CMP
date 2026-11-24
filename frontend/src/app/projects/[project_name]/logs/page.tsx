"use client";

import { LogsView } from "@/components/activity/LogsView";
import { logsQueryFromParams } from "@/components/activity/logsQuery";
import { projectHref } from "@/components/layout/projectSections";
import { PageHeader } from "@/components/layout/PageHeader";
import { ScrollText } from "lucide-react";
import { useTranslations } from "next-intl";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

function ProjectLogs() {
  const t = useTranslations("projects.logs");
  const tNav = useTranslations("projectNav");
  const params = useParams();
  const projectName = params.project_name as string;
  const search = useSearchParams();
  const namespace = search.get("namespace") ?? undefined;
  // Opened from an app: read that app's store.
  const app = Number(search.get("app"));

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={namespace && namespace !== projectName ? namespace : undefined}
        icon={ScrollText}
        breadcrumbs={[
          { label: tNav("projects"), href: "/projects" },
          { label: projectName, href: projectHref(projectName) },
          { label: t("title") },
        ]}
      />
      <main className="px-6 py-4">
        <LogsView
          project={projectName}
          namespace={namespace}
          appId={Number.isInteger(app) && app > 0 ? app : undefined}
          initialQuery={logsQueryFromParams(search)}
          fullPage
        />
      </main>
    </div>
  );
}

export default function ProjectLogsPage() {
  return (
    <Suspense>
      <ProjectLogs />
    </Suspense>
  );
}
