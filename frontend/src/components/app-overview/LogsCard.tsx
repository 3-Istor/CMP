"use client";

import { LogsView } from "@/components/activity/LogsView";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { OpenDashboardButton } from "@/components/ui/open-dashboard-button";
import { useTranslations } from "next-intl";

/** The overview's live tail: the logs tab's viewer, without its filters. */
export function LogsCard({
  project,
  namespace,
  base,
  allowed,
}: {
  project: string;
  namespace: string;
  base: string;
  allowed: boolean;
}) {
  const t = useTranslations("appPage.logs");
  if (!allowed) return null;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm">{t("recent")}</CardTitle>
        <OpenDashboardButton href={`${base}/logs`} label={t("open")} />
      </CardHeader>
      <CardContent>
        <LogsView project={project} namespace={namespace} compact />
      </CardContent>
    </Card>
  );
}
