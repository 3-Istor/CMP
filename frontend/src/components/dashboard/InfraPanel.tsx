"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { GlobalHealthResponse } from "@/types";
import { cn } from "@/lib/utils";
import { Server } from "lucide-react";
import { useTranslations } from "next-intl";

const DOT: Record<string, string> = {
  up: "bg-green-500",
  starting: "bg-amber-500",
  down: "bg-muted-foreground/50",
  unknown: "bg-muted-foreground/50",
};

function Row({
  name,
  detail,
  state,
  label,
}: {
  name: string;
  detail?: string | null;
  state: string;
  label: string;
}) {
  return (
    <li className="flex items-center gap-2 py-1.5 text-sm">
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", DOT[state] ?? DOT.unknown)}
        aria-hidden
      />
      <span className="font-mono text-xs">{name}</span>
      {detail && (
        <span className="truncate font-mono text-xs text-muted-foreground">
          {detail}
        </span>
      )}
      <span className="ml-auto text-xs text-muted-foreground">{label}</span>
    </li>
  );
}

/** The nodes and public clouds behind the platform: the old home card, compact. */
export function InfraPanel({
  health,
  loading,
}: {
  health: GlobalHealthResponse | null;
  loading: boolean;
}) {
  const t = useTranslations("overview.infra");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Server className="h-4 w-4" aria-hidden />
          {t("title")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading && !health ? (
          <Skeleton className="h-24 w-full" />
        ) : !health ? (
          <p className="text-sm text-muted-foreground">{t("unavailable")}</p>
        ) : (
          <ul className="divide-y">
            {health.openstack_hypervisors.map((hv) => (
              <Row
                key={hv.name}
                name={hv.name}
                detail={hv.ip}
                state={hv.state === "up" ? "up" : "down"}
                label={t(hv.state === "up" ? "states.up" : "states.down")}
              />
            ))}
            {health.clusters.map((c) => (
              <Row
                key={c.cloud}
                name={c.cloud === "aws" ? "EKS" : "GKE"}
                detail={c.cloud.toUpperCase()}
                state={c.state}
                label={t(`states.${c.state}`)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
