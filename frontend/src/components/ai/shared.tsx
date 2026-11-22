"use client";

import { intlLocale } from "@/components/finops/format";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AgentIsolation, AgentStatus, ToolRisk } from "@/types/ai";
import {
  Box,
  Code2,
  Globe,
  KeyRound,
  PenLine,
  ShieldAlert,
  ShieldCheck,
  ShieldHalf,
  type LucideIcon,
} from "lucide-react";
import { useFormatter, useLocale, useNow, useTranslations } from "next-intl";
import { useId } from "react";

export function useAiFormat() {
  const locale = intlLocale(useLocale());
  const formatter = useFormatter();
  const now = useNow({ updateInterval: 60_000 });
  return {
    relative: (value: string) => formatter.relativeTime(new Date(value), now),
    tokens: (value: number) =>
      new Intl.NumberFormat(locale, {
        notation: value >= 100_000 ? "compact" : "standard",
        maximumFractionDigits: 1,
      }).format(value),
    eur: (value: number) =>
      new Intl.NumberFormat(locale, {
        style: "currency",
        currency: "EUR",
        minimumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
        maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
      }).format(value),
    usd: (value: number) =>
      new Intl.NumberFormat(locale, {
        style: "currency",
        currency: "USD",
        currencyDisplay: "narrowSymbol",
        minimumFractionDigits: 2,
        maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
      }).format(value),
    ms: (value: number) =>
      value < 1000
        ? `${Math.round(value)} ms`
        : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1000)} s`,
  };
}

const STATUS_CLASS: Record<AgentStatus, string> = {
  active: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  suspended: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  error: "bg-destructive/10 text-destructive",
  pending: "bg-primary/10 text-primary",
};

const STATUS_DOT: Record<AgentStatus, string> = {
  active: "bg-emerald-500",
  suspended: "bg-amber-500",
  error: "bg-destructive",
  pending: "bg-primary animate-pulse",
};

export function AgentStatusBadge({ status, className }: { status: AgentStatus; className?: string }) {
  const t = useTranslations("ai.status");
  return (
    <Badge variant="secondary" className={cn(STATUS_CLASS[status], className)}>
      <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[status])} />
      {t(status)}
    </Badge>
  );
}

const ISOLATION_ICON: Record<AgentIsolation, LucideIcon> = {
  runc: Box,
  gvisor: ShieldHalf,
  kata: ShieldCheck,
};

export function IsolationBadge({ isolation }: { isolation: AgentIsolation }) {
  const t = useTranslations("ai.isolation");
  const Icon = ISOLATION_ICON[isolation];
  return (
    <Badge variant="outline" title={t(`${isolation}Hint`)}>
      <Icon aria-hidden />
      {t(isolation)}
    </Badge>
  );
}

export const RISK_META: Record<ToolRisk, { icon: LucideIcon; className: string }> = {
  untrusted_content: {
    icon: ShieldAlert,
    className: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  },
  private_data: { icon: KeyRound, className: "bg-violet-500/15 text-violet-800 dark:text-violet-300" },
  writes: { icon: PenLine, className: "bg-sky-500/15 text-sky-800 dark:text-sky-300" },
  external_send: { icon: Globe, className: "bg-rose-500/15 text-rose-800 dark:text-rose-300" },
  executes_code: { icon: Code2, className: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300" },
};

export function RiskBadge({ risk }: { risk: ToolRisk }) {
  const t = useTranslations("ai.risk");
  const { icon: Icon, className } = RISK_META[risk];
  return (
    <Badge variant="secondary" className={className}>
      <Icon aria-hidden />
      {t(risk)}
    </Badge>
  );
}

/** A 24-point area sparkline; decorative, the number next to it carries the value. */
export function Sparkline({
  points,
  className,
  tone = "primary",
}: {
  points: number[];
  className?: string;
  tone?: "primary" | "muted";
}) {
  const id = useId();
  const width = 120;
  const height = 32;
  if (points.length < 2) {
    return <div className={cn("h-8 w-[120px]", className)} aria-hidden />;
  }
  const max = Math.max(...points, 1);
  const step = width / (points.length - 1);
  const coords = points.map((p, i) => [i * step, height - 2 - (p / max) * (height - 4)] as const);
  const line = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  const color = tone === "primary" ? "var(--primary)" : "var(--muted-foreground)";
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("h-8 w-[120px] overflow-visible", className)}
      aria-hidden
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx={coords.at(-1)![0]} cy={coords.at(-1)![1]} r="2.2" fill={color} />
    </svg>
  );
}

export function gaugeTone(pct: number): string {
  if (pct >= 100) return "var(--destructive)";
  if (pct >= 80) return "oklch(0.72 0.17 65)";
  return "var(--primary)";
}

/** Ring gauge: value against a cap, as a meter for assistive technology. */
export function RingGauge({
  value,
  max,
  label,
  valueText,
  size = 120,
  children,
}: {
  value: number;
  max: number;
  label: string;
  valueText: string;
  size?: number;
  children?: React.ReactNode;
}) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const stroke = 10;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(value, max)}
      aria-valuetext={valueText}
      className="relative grid shrink-0 place-items-center"
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--muted)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={gaugeTone(pct)}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - pct / 100)}
          className="transition-[stroke-dashoffset] duration-700 ease-out motion-reduce:transition-none"
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">{children}</div>
    </div>
  );
}

export function BarGauge({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(value, max)}
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        className="h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none"
        style={{ width: `${pct}%`, backgroundColor: gaugeTone(pct) }}
      />
    </div>
  );
}
