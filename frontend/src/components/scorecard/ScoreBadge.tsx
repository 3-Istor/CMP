"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Scorecard } from "@/types";
import { useTranslations } from "next-intl";

const GRADE_STYLE: Record<Scorecard["grade"], string> = {
  A: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  B: "bg-lime-100 text-lime-800 dark:bg-lime-950 dark:text-lime-300",
  C: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300",
  D: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  E: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

export function ScoreBadge({
  card,
  className,
}: {
  card: Scorecard | undefined;
  className?: string;
}) {
  const t = useTranslations("scorecard");
  if (!card) return null;
  return (
    <Badge
      role="img"
      aria-label={t("badgeLabel", {
        grade: card.grade,
        score: card.score,
        max: card.max,
      })}
      className={cn("tabular-nums", GRADE_STYLE[card.grade], className)}
    >
      <span aria-hidden>
        {card.grade} {card.score}
      </span>
    </Badge>
  );
}
