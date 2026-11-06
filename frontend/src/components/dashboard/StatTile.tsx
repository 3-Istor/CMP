import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";

export function StatTile({
  href,
  icon: Icon,
  label,
  value,
  hint,
  tone = "default",
  loading = false,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "ok" | "warn" | "bad";
  loading?: boolean;
}) {
  return (
    <Link
      href={href}
      className="group rounded-xl border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-muted/30 focus-visible:outline-2 focus-visible:outline-ring"
    >
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Icon className="h-4 w-4" aria-hidden />
        {label}
      </div>
      {loading ? (
        <div className="mt-2 h-8 w-24 animate-pulse rounded bg-muted" />
      ) : (
        <p
          className={cn(
            "mt-1.5 text-2xl font-semibold tabular-nums tracking-tight",
            tone === "ok" && "text-green-700 dark:text-green-400",
            tone === "warn" && "text-amber-700 dark:text-amber-400",
            tone === "bad" && "text-red-700 dark:text-red-400",
          )}
        >
          {value}
        </p>
      )}
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </Link>
  );
}
