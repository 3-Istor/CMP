import { cn } from "@/lib/utils";
import { Star } from "lucide-react";
import { toneOf, type Tone } from "./ClusterGlyph";

export type AppKind = "web" | "api" | "db" | "worker" | "other";

const KIND: Record<AppKind, string> = {
  web: "bg-sky-500",
  api: "bg-violet-500",
  db: "bg-slate-600 dark:bg-slate-400",
  worker: "bg-amber-500",
  other: "bg-muted-foreground",
};
const DOT: Record<Tone, string> = {
  up: "bg-emerald-500",
  degraded: "bg-amber-500",
  down: "bg-destructive",
  neutral: "bg-muted-foreground/60",
};

/** Best-effort type of an app from its name / template hints. */
export function appKind(...hints: (string | null | undefined)[]): AppKind {
  const h = hints.filter(Boolean).join(" ").toLowerCase();
  if (/\b(db|database|postgres|mysql|mongo|redis|sql)/.test(h)) return "db";
  if (/\b(worker|job|queue|cron)/.test(h)) return "worker";
  if (/\b(api|backend|server|fastapi|grok)/.test(h)) return "api";
  if (/\b(web|front|site|static|landing|ui)/.test(h)) return "web";
  return "other";
}

/** App symbol: type square, name, health dot. */
export function AppChip({ name, kind, state, favorite, footprint }: { name: string; kind: AppKind; state?: string; favorite?: boolean; footprint?: React.ReactNode }) {
  return (
    <span className="inline-flex max-w-full flex-col gap-0.5 rounded-lg border bg-muted/40 px-2 py-0.5 text-xs" data-kind={kind}>
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className={cn("size-3 shrink-0 rounded-[3px]", KIND[kind])} />
        <span className="truncate font-medium">{name}</span>
        {favorite && <Star aria-hidden className="size-3 shrink-0 fill-amber-500 text-amber-500" />}
        <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", DOT[toneOf(state ?? "up")])} />
      </span>
      {footprint && <span className="pl-[18px]">{footprint}</span>}
    </span>
  );
}
