"use client";

import { Progress } from "@/components/ui/progress";
import {
  getObsDeploys,
  getObsDora,
  getObsSignals,
  getObsSlo,
  getObsTopErrors,
} from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { ErrorPattern, SloObjective, SloStatus } from "@/types/observability";
import { ExternalLink, Waypoints } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";
import { AlertsView } from "./AlertsView";
import { SignalChart } from "./SignalChart";
import {
  CardState,
  RangeSelect,
  Section,
  useObsCapabilities,
  useObsFormat,
} from "./shared";

const RANGES = ["1h", "6h", "24h", "7d"] as const;

/**
 * The top of the app's Overview once the stores exist (05-dashboards):
 * signals with deploy markers, error budget, delivery, top errors and the
 * alerts firing. Without a metrics store the page stays as it was.
 */
export function OverviewObservability({
  appId,
  project,
  app,
  base,
  allowed,
}: {
  appId: number;
  project: string;
  app: string;
  base: string;
  allowed: boolean;
}) {
  const caps = useObsCapabilities(appId, allowed);
  const [range, setRange] = useState<string>("24h");
  if (!allowed || !caps.data?.metrics) return null;
  return (
    <>
      <Signals appId={appId} range={range} setRange={setRange} grafana={caps.data.grafana_url} />
      <div className="grid gap-4 lg:grid-cols-2">
        {caps.data.slo && <Budget appId={appId} />}
        <Delivery appId={appId} />
      </div>
      {caps.data.logs && <TopErrors appId={appId} base={base} range="1h" />}
      {caps.data.alerts && (
        <AlertsView
          appId={appId}
          project={project}
          app={app}
          maxHours={caps.data.silence_max_hours}
          compact
        />
      )}
    </>
  );
}

function Signals({
  appId,
  range,
  setRange,
  grafana,
}: {
  appId: number;
  range: string;
  setRange: (r: string) => void;
  grafana: string;
}) {
  const t = useTranslations("obs.signals");
  const to = useTranslations("obs");
  const { number, percent, ms } = useObsFormat();
  const signals = useCardData(`obs-signals-${appId}-${range}`, () =>
    getObsSignals(appId, range),
  );
  const markers = useCardData(`obs-deploys-${appId}-${range}`, () =>
    getObsDeploys(appId, range),
  );
  return (
    <Section
      title={t("title")}
      description={t("description")}
      actions={
        <div className="flex items-center gap-3">
          <RangeSelect value={range} options={RANGES} onChange={setRange} />
          <a
            href={grafana}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            {to("openGrafana")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
      }
    >
      <CardState card={signals} signal="metrics">
        {(data) =>
          data.no_traffic ? (
            <p role="status" className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
              {t("noTraffic")}
            </p>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-3">
                <SignalChart
                  name={t("rate")}
                  points={data.rate}
                  markers={markers.data ?? []}
                  format={(v) => number(v, 2)}
                />
                <SignalChart
                  name={t("errors")}
                  points={data.error_percent}
                  markers={markers.data ?? []}
                  format={(v) => percent(v / 100, 2)}
                  color="var(--destructive)"
                />
                <SignalChart
                  name={t("p95")}
                  points={data.p95_ms}
                  markers={markers.data ?? []}
                  format={ms}
                  color="var(--chart-2)"
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="mb-2 text-left text-sm font-semibold">
                    {t("routes")}
                  </caption>
                  <thead className="text-left text-xs text-muted-foreground">
                    <tr>
                      <th scope="col" className="py-1 pr-3 font-medium">{t("route")}</th>
                      <th scope="col" className="py-1 pr-3 text-right font-medium">{t("rate")}</th>
                      <th scope="col" className="py-1 pr-3 text-right font-medium">{t("errors")}</th>
                      <th scope="col" className="py-1 pr-3 text-right font-medium">{t("p50")}</th>
                      <th scope="col" className="py-1 text-right font-medium">{t("p95")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {data.routes.length === 0 && (
                      <tr>
                        <td colSpan={5} className="py-2 text-muted-foreground">
                          {t("noRoutes")}
                        </td>
                      </tr>
                    )}
                    {data.routes.map((r) => (
                      <tr key={r.route}>
                        <th scope="row" className="py-1.5 pr-3 text-left font-mono text-xs font-normal">
                          {r.route}
                        </th>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{number(r.requests_per_second, 2)}</td>
                        <td
                          className={cn(
                            "py-1.5 pr-3 text-right tabular-nums",
                            r.error_percent >= 1 && "font-medium text-destructive",
                          )}
                        >
                          {percent(r.error_percent / 100, 2)}
                        </td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{r.p50_ms == null ? "-" : ms(r.p50_ms)}</td>
                        <td className="py-1.5 text-right tabular-nums">{r.p95_ms == null ? "-" : ms(r.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
        }
      </CardState>
    </Section>
  );
}

function Budget({ appId }: { appId: number }) {
  const t = useTranslations("obs.slo");
  const card = useCardData(`obs-slo-${appId}`, () => getObsSlo(appId, "7d"));
  return (
    <Section
      title={t("title")}
      description={card.data ? t("description", { days: card.data.settings.period_days }) : undefined}
    >
      <CardState card={card} signal="slo" height="h-32">
        {(data) => (
          <div className="space-y-4">
            {data.objectives.map((o) => (
              <ObjectiveRow key={o.kind} objective={o} status={data} />
            ))}
            <p className="text-xs text-muted-foreground">{t("burnHint")}</p>
          </div>
        )}
      </CardState>
    </Section>
  );
}

function ObjectiveRow({ objective, status }: { objective: SloObjective; status: SloStatus }) {
  const t = useTranslations("obs.slo");
  const { number, percent } = useObsFormat();
  const left = objective.budget_remaining;
  const name =
    objective.kind === "availability"
      ? t("availability")
      : t("latency", { ms: status.settings.latency_threshold_ms });
  const burn = (value: number | null) => (value == null ? "-" : `${number(value, 1)}x`);
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">{name}</span>
        <span className="text-xs text-muted-foreground">
          {t("target", { value: number(objective.target, 2) })}
        </span>
      </div>
      {left == null ? (
        <p className="text-sm text-muted-foreground">{t("noData")}</p>
      ) : (
        <>
          <Progress
            value={Math.max(0, Math.min(100, left * 100))}
            aria-label={`${name}: ${t("remaining", { value: percent(Math.max(0, left), 0) })}`}
            className={cn(left < 0.25 && "[&_[data-slot=progress-indicator]]:bg-destructive")}
          />
          <div className="flex flex-wrap justify-between gap-2 text-xs">
            <span className={cn("font-medium", left <= 0 && "text-destructive")}>
              {left <= 0 ? t("exhausted") : t("remaining", { value: percent(left, 0) })}
            </span>
            <span className="tabular-nums text-muted-foreground">
              {t("burn1h")} {burn(objective.burn_rate_1h)} · {t("burn6h")} {burn(objective.burn_rate_6h)}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function Delivery({ appId }: { appId: number }) {
  const t = useTranslations("obs.delivery");
  const { number, percent } = useObsFormat();
  const card = useCardData(`obs-dora-${appId}`, () => getObsDora(appId, 30));
  return (
    <Section title={t("title")}>
      <CardState card={card} signal="metrics" height="h-32">
        {(d) =>
          d.deployments + d.failed_changes === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none", { days: d.days })}</p>
          ) : (
            <div className="space-y-3">
              <dl className="grid grid-cols-2 gap-4">
                <div>
                  <dt className="text-xs text-muted-foreground">{t("deploysPerWeek")}</dt>
                  <dd className="text-2xl font-semibold tabular-nums">{number(d.deploys_per_week, 1)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">{t("changeFailure")}</dt>
                  <dd className="text-2xl font-semibold tabular-nums">
                    {d.change_failure_rate == null ? "-" : percent(d.change_failure_rate, 0)}
                  </dd>
                </div>
              </dl>
              <p className="text-xs text-muted-foreground">
                {t("window", { days: d.days, deploys: d.deployments, failed: d.failed_changes })}
              </p>
            </div>
          )
        }
      </CardState>
    </Section>
  );
}

export function TopErrors({
  appId,
  base,
  range,
  onPick,
  actions,
}: {
  appId: number;
  base: string;
  range: string;
  onPick?: (row: ErrorPattern) => void;
  actions?: React.ReactNode;
}) {
  const t = useTranslations("obs.topErrors");
  const { time } = useObsFormat();
  const card = useCardData(`obs-top-${appId}-${range}`, () => getObsTopErrors(appId, range, 5));
  return (
    <Section
      title={t("title")}
      description={t("description")}
      actions={
        actions ?? (
          <Link href={`${base}/logs`} className="text-sm text-primary hover:underline">
            {t("seeAll")}
          </Link>
        )
      }
    >
      <CardState card={card} signal="logs" height="h-28">
        {(rows) =>
          rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <ol className="divide-y rounded-xl border">
              {rows.map((r) => (
                <li key={r.pattern} className="flex flex-col gap-1 p-3 sm:flex-row sm:items-center sm:gap-3">
                  <span className="w-20 shrink-0 text-sm font-semibold tabular-nums text-destructive">
                    {t("count", { count: r.count })}
                  </span>
                  {onPick ? (
                    <button
                      type="button"
                      onClick={() => onPick(r)}
                      className="min-w-0 flex-1 break-all text-left font-mono text-xs hover:underline focus-visible:underline"
                    >
                      {r.pattern}
                    </button>
                  ) : (
                    <code className="min-w-0 flex-1 break-all text-xs">{r.pattern}</code>
                  )}
                  <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                    {r.last_seen && t("lastSeen", { time: time(r.last_seen) })}
                    {r.trace_id && (
                      <Link
                        href={`${base}/traces?trace=${r.trace_id}`}
                        className="inline-flex items-center gap-1 text-primary hover:underline"
                      >
                        <Waypoints className="h-3.5 w-3.5" aria-hidden />
                        {t("openTrace")}
                      </Link>
                    )}
                  </span>
                </li>
              ))}
            </ol>
          )
        }
      </CardState>
    </Section>
  );
}
