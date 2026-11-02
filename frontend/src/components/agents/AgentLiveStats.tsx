"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { getAgentPublicStats } from "@/lib/api";
import type { AgentPublicStats } from "@/types";

/** Real figures from the platform, shown only once there is something to show. */
export function AgentLiveStats() {
  const t = useTranslations("agents");
  const [stats, setStats] = useState<AgentPublicStats | null>(null);
  useEffect(() => {
    getAgentPublicStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);
  if (!stats || stats.apps_created_this_month === 0) return null;
  return (
    <p className="mt-6 text-sm text-muted-foreground">
      <span className="font-heading text-lg font-bold text-primary">
        {stats.apps_created_this_month}
      </span>{" "}
      {t("liveStats", { assistants: stats.connected_assistants })}
    </p>
  );
}
