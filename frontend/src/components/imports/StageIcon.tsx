"use client";

import { CircleCheck, CircleDashed, CircleSlash, CircleX, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import type { StageState } from "@/lib/importPlan";

const ICONS = {
  pending: CircleDashed,
  active: Loader2,
  done: CircleCheck,
  failed: CircleX,
  skipped: CircleSlash,
};

const COLORS: Record<StageState, string> = {
  pending: "text-muted-foreground",
  active: "animate-spin text-primary",
  done: "text-green-600 dark:text-green-400",
  failed: "text-destructive",
  skipped: "text-muted-foreground",
};

export function StageIcon({ state }: { state: StageState }) {
  const t = useTranslations("imports.stageState");
  const Icon = ICONS[state];
  return (
    <span role="img" aria-label={t(state)} title={t(state)} className="inline-flex">
      <Icon className={cn("h-4 w-4", COLORS[state])} aria-hidden />
    </span>
  );
}
