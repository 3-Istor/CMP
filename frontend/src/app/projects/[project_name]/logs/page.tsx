"use client";

import { LogsView } from "@/components/activity/LogsView";
import { PageHeader } from "@/components/layout/PageHeader";
import { ScrollText } from "lucide-react";
import { useTranslations } from "next-intl";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

function ProjectLogs() {
  const t = useTranslations("projects.logs");
  const params = useParams();
  const projectName = params.project_name as string;
  const namespace = useSearchParams().get("namespace") ?? undefined;

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={namespace ?? projectName}
        icon={ScrollText}
      />
      <main className="px-6 py-4">
        <LogsView project={projectName} namespace={namespace} fullPage />
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
