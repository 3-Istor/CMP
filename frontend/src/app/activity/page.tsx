"use client";

import { History } from "lucide-react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";

import { ActivityTab } from "@/components/activity/ActivityTab";
import { AuditView } from "@/components/activity/AuditView";
import { PlatformCard } from "@/components/activity/PlatformCard";
import { getPlatformStatus } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjects } from "@/lib/hooks";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

const SELECT =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

const PLATFORM = "__platform__";

function ActivitySkeleton() {
  return (
    <div className="space-y-4" aria-busy>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
      <Skeleton className="h-48" />
      <div className="space-y-2">
        {Array.from({ length: 20 }, (_, i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>
    </div>
  );
}

function ActivityInner() {
  const t = useTranslations("activity.page");
  const tc = useTranslations("common");
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { projects, loading: projectsLoading } = useProjects();
  const [isCnpAdmin, setIsCnpAdmin] = useState(false);
  const [adminKnown, setAdminKnown] = useState(false);

  useEffect(() => {
    getPlatformStatus()
      .then(
        () => setIsCnpAdmin(true),
        () => setIsCnpAdmin(false),
      )
      .finally(() => setAdminKnown(true));
  }, []);

  const selected =
    searchParams.get("project") ??
    (isCnpAdmin ? PLATFORM : (projects[0]?.name ?? ""));

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={isCnpAdmin ? t("descriptionAll") : t("description")}
        icon={History}
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
              {isCnpAdmin && (
                <option value={PLATFORM}>{t("platformOption")}</option>
              )}
              {projects.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
        }
      />
      <PageBody>
      {!searchParams.get("project") && (projectsLoading || !adminKnown) ? (
        <ActivitySkeleton />
      ) : selected === PLATFORM ? (
        <>
          <PlatformCard />
          <AuditView project={null} canExport showLogins />
        </>
      ) : selected ? (
        <ActivityTab key={selected} project={selected} />
      ) : (
        <p className="text-sm text-muted-foreground">{tc("noProjects")}</p>
      )}
      </PageBody>
    </div>
  );
}

export default function ActivityPage() {
  return (
    <Suspense>
      <ActivityInner />
    </Suspense>
  );
}
