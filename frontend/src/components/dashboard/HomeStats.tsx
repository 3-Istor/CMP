"use client";

import { formatEUR, intlLocale } from "@/components/finops/format";
import { useFinopsOverview, useSecurityOverview } from "@/lib/hooks";
import type { Deployment, GlobalHealthResponse } from "@/types";
import { Boxes, Cloud, Shield, Wallet } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { StatTile } from "./StatTile";

export function HomeStats({
  apps,
  loading,
  health,
}: {
  apps: Deployment[];
  loading: boolean;
  health: GlobalHealthResponse | null;
}) {
  const t = useTranslations("overview.stats");
  const locale = intlLocale(useLocale());
  const security = useSecurityOverview();
  const { data: finops, loading: finopsLoading } = useFinopsOverview();

  const running = apps.filter((a) => a.status === "running").length;
  const failed = apps.filter((a) => a.status === "failed").length;
  const actions = security.reduce((sum, p) => sum + p.actions, 0);
  const core = security.reduce((sum, p) => sum + p.core, 0);
  const nodes = health?.openstack_hypervisors ?? [];
  const nodesUp = nodes.filter((n) => n.state === "up").length;
  const cloudsUp = (health?.clusters ?? []).filter((c) => c.state === "up").length;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        href="/apps"
        icon={Boxes}
        label={t("apps")}
        loading={loading && apps.length === 0}
        value={t("appsValue", { running, total: apps.length })}
        hint={failed > 0 ? t("appsFailed", { count: failed }) : t("appsOk")}
        tone={failed > 0 ? "bad" : "default"}
      />
      <StatTile
        href="/security"
        icon={Shield}
        label={t("security")}
        value={actions}
        hint={core > 0 ? t("securityCore", { count: core }) : t("securityHint")}
        tone={core > 0 ? "bad" : actions > 0 ? "warn" : "ok"}
      />
      <StatTile
        href="/finops"
        icon={Wallet}
        label={t("costs")}
        loading={finopsLoading && !finops}
        value={formatEUR(finops?.summary.month_to_date_eur ?? 0, "EUR", locale)}
        hint={
          finops
            ? t("costsForecast", {
                amount: formatEUR(finops.summary.projected_month_eur, "EUR", locale),
              })
            : undefined
        }
      />
      <StatTile
        href="/activity"
        icon={Cloud}
        label={t("platform")}
        loading={!health}
        value={nodes.length > 0 ? t("nodesValue", { up: nodesUp, total: nodes.length }) : "-"}
        hint={t("cloudsHint", { count: cloudsUp })}
        tone={nodes.length > 0 && nodesUp < nodes.length ? "warn" : "default"}
      />
    </div>
  );
}
