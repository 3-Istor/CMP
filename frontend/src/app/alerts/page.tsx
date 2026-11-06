"use client";

import { BellRing } from "lucide-react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";

import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import { useProjects } from "@/lib/hooks";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

const SELECT =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

function AlertsInner() {
  const t = useTranslations("alerts.page");
  const tc = useTranslations("common");
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { projects } = useProjects();
  const selected = searchParams.get("project") ?? projects[0]?.name ?? "";

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={t("description")}
        icon={BellRing}
        actions={
            <select
              aria-label={tc("project")}
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
        }
      />
      <PageBody>
      {selected ? (
        <AlertsPanel key={selected} project={selected} />
      ) : (
        <p className="text-sm text-muted-foreground">{tc("noProjects")}</p>
      )}
      </PageBody>
    </div>
  );
}

export default function AlertsPage() {
  return (
    <Suspense>
      <AlertsInner />
    </Suspense>
  );
}
