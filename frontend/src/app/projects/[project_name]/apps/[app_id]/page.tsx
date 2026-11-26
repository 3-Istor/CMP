"use client";

import { LogsView } from "@/components/activity/LogsView";
import { DeploymentsPanel, AlertsPanel, ClientsPanel, TalksToPanel, TopErrorsPanel } from "@/components/app-overview/Cards";
import { LogVolumeCard, ResourcesCard, TrafficCard } from "@/components/app-overview/Charts";
import { appKind } from "@/components/cluster/AppChip";
import { EventsCard } from "@/components/app-overview/EventsCard";
import { useRollback } from "@/components/app-overview/Rollback";
import { Panel } from "@/components/app-overview/shared";
import { DatastoreStatusBand, WebStatusBand } from "@/components/app-overview/StatusBand";
import { WeeklyView } from "@/components/app-overview/WeeklyView";
import { ManagedServicesCard } from "@/components/imports/Recommendations";
import { useAppContext, useAppEnv } from "@/components/projects/AppContext";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SummaryRange } from "@/lib/api";
import { useAppSummary, useInView } from "@/lib/appSummary";
import { hasScorecard } from "@/lib/scorecard";
import { useTranslations } from "next-intl";
import { useState } from "react";

export default function AppOverviewPage() {
  const { appId, projectName, deployment: current } = useAppContext();
  const { can } = useProjectContext();
  const t = useTranslations("appOverview");

  const isKubernetes = current.provider_type === "kubernetes";
  const canViewLogs = can("app.viewLogs");
  const canViewObservability = can("app.viewObservability");
  const allowed = isKubernetes && canViewLogs;
  const base = `/projects/${encodeURIComponent(projectName)}/apps/${appId}`;

  const env = useAppEnv();
  const staging = env === "staging";
  const [range, setRange] = useState<SummaryRange>("24h");
  const day = useAppSummary(appId, "24h", allowed, env);
  const chart = useAppSummary(appId, range, allowed && range !== "24h", env);
  const chartCard = range === "24h" ? day : chart;
  const summary = day.data;
  const loading = day.loading && !summary;
  const chartLoading = chartCard.loading && !chartCard.data;

  // Rollback acts on prod: not offered on staging deploys.
  const rollback = useRollback(appId, summary?.deploys ?? [], allowed && !staging && can("release.operate"));
  const [belowRef, belowSeen] = useInView<HTMLDivElement>();

  if (!isKubernetes) {
    return can("app.viewConfig") ? <TerraformOutputs outputs={current.terraform_outputs} /> : null;
  }
  if (!allowed) return null;

  // Until the summary answers, guess the layout from the template so the skeleton keeps its shape.
  const kind =
    summary?.kind ??
    (appKind(current.name, current.template_id, current.template_category) === "db" ? "datastore" : "web");
  const datastore = kind === "datastore";
  const prodNamespace = current.k8s_namespace ?? `${projectName}-${current.name}`;
  const namespace = staging ? `${prodNamespace}-stg` : prodNamespace;
  const alertsHref = `/projects/${encodeURIComponent(projectName)}/alerts`;

  return (
    <>
      {datastore ? (
        <DatastoreStatusBand summary={summary} loading={loading} />
      ) : (
        <WebStatusBand summary={summary} loading={loading} />
      )}

      {datastore ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <ResourcesCard
            summary={chartCard.data}
            loading={chartLoading}
            range={range}
            onRange={setRange}
            className="lg:col-span-2"
          />
          <ClientsPanel summary={summary} loading={loading} />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <TrafficCard
            summary={chartCard.data}
            loading={chartLoading}
            range={range}
            onRange={setRange}
            className="lg:col-span-2"
          />
          <DeploymentsPanel
            deploys={summary?.deploys ?? []}
            loading={loading}
            base={base}
            promotionFor={rollback.promotionFor}
            onRollback={rollback.ask}
          />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <LogVolumeCard summary={summary} loading={loading} />
        <Panel title={t("recentLogs")} className="lg:col-span-2">
          <LogsView project={projectName} namespace={namespace} appId={appId} env={env} variant="compact" />
        </Panel>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <TopErrorsPanel summary={summary} loading={loading} base={base} staging={staging} />
        <AlertsPanel summary={summary} loading={loading} alertsHref={alertsHref} staging={staging} />
        {datastore ? (
          <DeploymentsPanel
            deploys={summary?.deploys ?? []}
            loading={loading}
            base={base}
            promotionFor={rollback.promotionFor}
            onRollback={rollback.ask}
          />
        ) : (
          <TalksToPanel summary={summary} loading={loading} />
        )}
      </div>

      <WeeklyView
        appId={appId}
        projectName={projectName}
        base={base}
        env={env}
        repoUrl={current.github_repo_url}
        canViewObservability={canViewObservability}
        canViewFinops={can("finops.view")}
        hasScore={hasScorecard(current)}
      />

      <div ref={belowRef} className="space-y-6">
        {belowSeen && current.template_id.startsWith("import-") && (
          <ManagedServicesCard
            projectName={projectName}
            app={current.name}
            canManage={can("import.manage")}
          />
        )}
        {belowSeen && <EventsCard appId={appId} env={env} allowed />}
      </div>

      {rollback.dialog}
    </>
  );
}

function TerraformOutputs({ outputs }: { outputs: string | null }) {
  const parsed = (() => {
    try {
      return outputs ? (JSON.parse(outputs) as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  })();
  const t = useTranslations("appPage.legacy");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("outputs")}</CardTitle>
      </CardHeader>
      <CardContent>
        {parsed ? (
          <div className="space-y-1.5 font-mono text-xs">
            {Object.entries(parsed).map(([key, value]) => (
              <div key={key} className="flex flex-wrap gap-2">
                <span className="shrink-0 text-muted-foreground">{key}:</span>
                {typeof value === "string" && value.startsWith("http") ? (
                  <a
                    href={value}
                    target="_blank"
                    rel="noreferrer"
                    className="break-all text-primary underline"
                  >
                    {value}
                  </a>
                ) : (
                  <span className="break-all">{String(value)}</span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("noOutputs")}</p>
        )}
      </CardContent>
    </Card>
  );
}
