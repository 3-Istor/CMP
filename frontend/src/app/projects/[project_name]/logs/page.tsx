"use client";

import { LogsView } from "@/components/activity/LogsView";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function ProjectLogsPage() {
  const params = useParams();
  const projectName = params.project_name as string;

  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-3 border-b px-6 py-3">
        <Link
          href={`/projects/${projectName}`}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> {projectName}
        </Link>
        <h1 className="text-base font-semibold">Logs</h1>
      </header>
      <main className="p-4">
        <LogsView project={projectName} fullPage />
      </main>
    </div>
  );
}
