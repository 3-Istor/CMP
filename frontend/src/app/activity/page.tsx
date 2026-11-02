"use client";

import { ActivityTab } from "@/components/activity/ActivityTab";
import { AuditView } from "@/components/activity/AuditView";
import { PlatformCard } from "@/components/activity/PlatformCard";
import { getPlatformStatus } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

const SELECT =
  "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

const PLATFORM = "__platform__";

function ActivityInner() {
  const t = useTranslations("activity.page");
  const tc = useTranslations("common");
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { projects } = useProjects();
  const [isCnpAdmin, setIsCnpAdmin] = useState(false);

  useEffect(() => {
    getPlatformStatus().then(
      () => setIsCnpAdmin(true),
      () => setIsCnpAdmin(false),
    );
  }, []);

  const selected =
    searchParams.get("project") ??
    (isCnpAdmin ? PLATFORM : (projects[0]?.name ?? ""));

  return (
    <main className="mx-auto max-w-7xl space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">
            {isCnpAdmin ? t("descriptionAll") : t("description")}
          </p>
        </div>
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
      </div>
      {selected === PLATFORM ? (
        <>
          <PlatformCard />
          <AuditView project={null} canExport showLogins />
        </>
      ) : selected ? (
        <ActivityTab key={selected} project={selected} />
      ) : (
        <p className="text-sm text-muted-foreground">{tc("noProjects")}</p>
      )}
    </main>
  );
}

export default function ActivityPage() {
  return (
    <Suspense>
      <ActivityInner />
    </Suspense>
  );
}
