import { cn } from "@/lib/utils";
import Link from "next/link";
import type { ReactNode } from "react";
import { Sparkline } from "./Sparkline";

export type StatTone = "ok" | "warn" | "bad";

export interface StatTileProps {
  label: string;
  /** Already formatted by the caller; null shows a dash. */
  value: ReactNode | null;
  unit?: string;
  /** One short line under the value: a delta, a limit, a reason. */
  hint?: ReactNode;
  sparkline?: (number | null)[];
  tone?: StatTone;
  /** Makes the whole tile a link to the detail. */
  href?: string;
  loading?: boolean;
  className?: string;
}

const TONE_VALUE: Record<StatTone, string> = {
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  bad: "text-red-700 dark:text-red-400",
};

const TONE_BORDER: Record<StatTone, string> = {
  ok: "",
  warn: "border-amber-500/50",
  bad: "border-red-500/60",
};

export function StatTile({
  label,
  value,
  unit,
  hint,
  sparkline,
  tone,
  href,
  loading = false,
  className,
}: StatTileProps) {
  const body = (
    <>
      <p className="truncate text-xs font-medium text-muted-foreground">{label}</p>
      {loading ? (
        <div className="my-1 h-7 w-24 animate-pulse rounded bg-muted" aria-hidden />
      ) : (
        <p
          className={cn(
            "text-2xl font-semibold tabular-nums",
            tone && TONE_VALUE[tone],
          )}
        >
          {value ?? "-"}
          {unit && value !== null && (
            <span className="ml-1 text-sm font-normal text-muted-foreground">
              {unit}
            </span>
          )}
        </p>
      )}
      {sparkline && (
        <Sparkline
          values={sparkline}
          label={label}
          loading={loading}
          className="h-8 w-full"
        />
      )}
      {hint &&
        (loading ? (
          <div className="h-4 w-32 animate-pulse rounded bg-muted" aria-hidden />
        ) : (
          <p className="truncate text-xs text-muted-foreground">{hint}</p>
        ))}
    </>
  );
  const classes = cn(
    "flex min-w-0 flex-col gap-1 rounded-xl border bg-card p-3",
    tone && TONE_BORDER[tone],
    href && "transition-colors hover:bg-muted/50",
    className,
  );
  return href ? (
    <Link href={href} className={classes} aria-busy={loading}>
      {body}
    </Link>
  ) : (
    <div className={classes} aria-busy={loading}>
      {body}
    </div>
  );
}
