"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { intlLocale } from "@/components/finops/format";
import { getObsCapabilities } from "@/lib/api";
import { useCardData, type CardData } from "@/lib/useCardData";
import type { ObsCapabilities } from "@/types/observability";
import { CircleSlash } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useId } from "react";

export const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

export type Signal = "metrics" | "logs" | "traces" | "alerts" | "slo";

const capabilityCache = new Map<number, Promise<ObsCapabilities>>();

/** Which tabs have a store behind them; one request per app per page load. */
export function useObsCapabilities(appId: number, enabled: boolean) {
  return useCardData<ObsCapabilities>(
    `obs-capabilities-${appId}`,
    () => {
      let found = capabilityCache.get(appId);
      if (!found) {
        found = getObsCapabilities(appId);
        capabilityCache.set(appId, found);
        found.catch(() => capabilityCache.delete(appId));
      }
      return found;
    },
    { enabled },
  );
}

export function NotAvailable({ signal }: { signal: Signal }) {
  const t = useTranslations("obs.notAvailable");
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-xl border border-dashed p-4 text-sm"
    >
      <CircleSlash className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div>
        <p className="font-medium">{t("title")}</p>
        <p className="text-muted-foreground">{t(signal)}</p>
      </div>
    </div>
  );
}

function detailOf(message: string): string {
  try {
    const detail = JSON.parse(message).detail;
    return typeof detail === "string" ? detail : message;
  } catch {
    return message;
  }
}

/** Loading, not configured, failed: the three states every card shares. */
export function CardState<T>({
  card,
  signal,
  height = "h-40",
  children,
}: {
  card: CardData<T>;
  signal: Signal;
  height?: string;
  children: (data: T) => React.ReactNode;
}) {
  const t = useTranslations("obs");
  if (card.loading && !card.data) return <Skeleton className={`${height} w-full rounded-xl`} />;
  if (card.error && !card.data) {
    if (card.error.includes('"not_available"')) return <NotAvailable signal={signal} />;
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 p-4 text-sm">
        <span>{t("error", { message: detailOf(card.error) })}</span>
        <Button variant="outline" size="sm" onClick={card.retry}>
          {t("retry")}
        </Button>
      </div>
    );
  }
  if (!card.data) return null;
  return <>{children(card.data)}</>;
}

export function RangeSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
}) {
  const t = useTranslations("obs.range");
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {t("label")}
      </label>
      <select
        id={id}
        className={SELECT_CLASS}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {t(o)}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Section({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <Card aria-labelledby={id}>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id={id} className="text-base font-semibold">
              {title}
            </h2>
            {description && (
              <p className="text-sm text-muted-foreground">{description}</p>
            )}
          </div>
          {actions}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

export function useObsFormat() {
  const locale = intlLocale(useLocale());
  const number = (value: number, digits = 1) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value);
  const percent = (ratio: number, digits = 1) =>
    new Intl.NumberFormat(locale, {
      style: "percent",
      maximumFractionDigits: digits,
    }).format(ratio);
  const ms = (value: number) =>
    value >= 1000 ? `${number(value / 1000, 2)} s` : `${number(value, 0)} ms`;
  const time = (iso: string | Date) =>
    new Date(iso).toLocaleString(locale, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  const clock = (iso: string | Date) =>
    new Date(iso).toLocaleTimeString(locale, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  const euro = (value: number, digits = 2) =>
    new Intl.NumberFormat(locale, {
      style: "currency",
      currency: "EUR",
      maximumFractionDigits: digits,
    }).format(value);
  return { number, percent, ms, time, clock, euro, locale };
}
