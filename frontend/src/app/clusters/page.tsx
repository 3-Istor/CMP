"use client";

import { ClusterOverview } from "@/components/cluster/ClusterOverview";
import { EmergencyPanel } from "@/components/cluster/EmergencyPanel";
import { MovesTable } from "@/components/cluster/MovesTable";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getEmergency, getPlatformPlacement, type Emergency, type PlatformPlacement } from "@/lib/api";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { Map as MapIcon, Siren } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const POLL_MS = 5000;

export default function ClustersPage() {
  const t = useTranslations("clusters");
  const isAdmin = usePlatformAdmin();
  const [placement, setPlacement] = useState<PlatformPlacement | null>(null);
  const [emergency, setEmergency] = useState<Emergency | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    const load = () =>
      Promise.allSettled([getPlatformPlacement(), getEmergency()]).then(([p, e]) => {
        if (p.status === "fulfilled") setPlacement(p.value);
        if (e.status === "fulfilled") setEmergency(e.value);
        setFailed(p.status === "rejected" || e.status === "rejected");
      });
    void load();
    return pollWhileVisible(load, POLL_MS);
  }, [isAdmin]);

  const down = (emergency?.clusters ?? []).filter((c) => c.state !== "up").length;
  const memory = Object.fromEntries((placement?.clusters ?? []).map((c) => [c.cloud, c.memory_requested_pct]));
  const overview = (
    <div className="space-y-6">
      <ClusterOverview data={placement} />
      <MovesTable moves={emergency?.moves ?? []} loading={!emergency} />
    </div>
  );

  return (
    <div className="min-h-screen">
      <PageHeader title={t("title")} icon={MapIcon} />
      <PageBody>
        {!isAdmin ? null : (
          <div className="space-y-5">
            {failed && (
              <p role="alert" className="text-sm text-destructive">
                {t("loadFailed")}
              </p>
            )}
            {emergency && down > 0 ? (
              // Mounted when the incident starts, so it opens on Emergency.
              <Tabs defaultValue="emergency">
                <TabsList>
                  <TabsTrigger value="overview">{t("overview")}</TabsTrigger>
                  <TabsTrigger value="emergency" className="gap-1.5 data-active:text-destructive">
                    <Siren aria-hidden className="size-4" />
                    {t("emergency")}
                    <span
                      aria-label={t("clustersDown", { count: down })}
                      className="grid h-4 min-w-4 place-items-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white"
                    >
                      {down}
                    </span>
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="overview" className="mt-5">
                  {overview}
                </TabsContent>
                <TabsContent value="emergency" className="mt-5 space-y-6">
                  <EmergencyPanel data={emergency} memory={memory} />
                  <MovesTable moves={emergency.moves} loading={false} />
                </TabsContent>
              </Tabs>
            ) : (
              overview
            )}
          </div>
        )}
      </PageBody>
    </div>
  );
}
