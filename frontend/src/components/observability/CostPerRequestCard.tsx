"use client";

import { Progress } from "@/components/ui/progress";
import { getObsCosts } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { useTranslations } from "next-intl";
import { CardState, Section, useObsFormat } from "./shared";

function UsageBar({
  label,
  idle,
}: {
  label: string;
  idle: number | null;
}) {
  const t = useTranslations("obs.costs");
  const { percent } = useObsFormat();
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        <span>{label}</span>
        {idle != null && (
          <span className={idle > 0.5 ? "font-medium text-amber-800 dark:text-amber-200" : "text-muted-foreground"}>
            {t("idle", { value: percent(idle, 0) })}
          </span>
        )}
      </div>
      {idle != null && <Progress value={Math.round((1 - idle) * 100)} aria-label={label} />}
    </div>
  );
}

/** Costs tab: cost per 1,000 requests and requests against real usage (D19). */
export function CostPerRequestCard({ appId }: { appId: number }) {
  const t = useTranslations("obs.costs");
  const { number, euro } = useObsFormat();
  const card = useCardData(`obs-costs-${appId}`, () => getObsCosts(appId));
  return (
    <Section
      title={t("title")}
      description={card.data ? t("description", { days: card.data.days }) : undefined}
    >
      <CardState card={card} signal="metrics">
        {(c) => (
          <div className="space-y-5">
            <dl className="grid gap-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">{t("per1k")}</dt>
                <dd className="text-2xl font-semibold tabular-nums">
                  {c.cost_per_1k_requests_eur == null ? "-" : euro(c.cost_per_1k_requests_eur, 4)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("total", { days: c.days })}</dt>
                <dd className="text-2xl font-semibold tabular-nums">{euro(c.cost_eur)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("requests", { days: c.days })}</dt>
                <dd className="text-2xl font-semibold tabular-nums">
                  {c.requests == null ? "-" : number(c.requests, 0)}
                </dd>
              </div>
            </dl>
            {!c.metrics_available ? (
              <p className="text-sm text-muted-foreground">{t("noMetrics")}</p>
            ) : (
              c.cost_per_1k_requests_eur == null && (
                <p className="text-sm text-muted-foreground">{t("noTraffic")}</p>
              )
            )}
            <div className="space-y-3">
              <h3 className="text-sm font-semibold">{t("waste")}</h3>
              {c.usage?.cpu_request_cores ? (
                <>
                  <UsageBar
                    label={t("cpu", {
                      used: number(c.usage.cpu_p95_cores ?? 0, 2),
                      requested: number(c.usage.cpu_request_cores, 2),
                    })}
                    idle={c.cpu_idle_ratio}
                  />
                  {c.usage.ram_request_gib ? (
                    <UsageBar
                      label={t("ram", {
                        used: number(c.usage.ram_p99_gib ?? 0, 2),
                        requested: number(c.usage.ram_request_gib, 2),
                      })}
                      idle={c.ram_idle_ratio}
                    />
                  ) : null}
                </>
              ) : (
                <p className="text-sm text-muted-foreground">{t("noUsage")}</p>
              )}
            </div>
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">{t("hints")}</h3>
              {c.recommendations.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("noHints")}</p>
              ) : (
                <ul className="divide-y rounded-xl border">
                  {c.recommendations.map((r) => (
                    <li key={`${r.rec_type}-${r.title}`} className="flex flex-wrap justify-between gap-2 p-3 text-sm">
                      <div className="min-w-0">
                        <p className="font-medium">{r.title}</p>
                        <p className="text-xs text-muted-foreground">{r.justification}</p>
                      </div>
                      <span className="shrink-0 font-medium tabular-nums">
                        {t("monthly", { amount: euro(r.monthly_saving_eur) })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </CardState>
    </Section>
  );
}
