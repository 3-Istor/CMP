"use client";

import { projectHref } from "@/components/layout/projectSections";
import { getProjectAi } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

/** "Accès IA" next to an app's name when the project granted it model access. */
export function AiAccessBadge({ project, app }: { project: string; app: string }) {
  const t = useTranslations("ai.appBadge");
  const ai = useCardData(`ai-${project}`, () => getProjectAi(project));
  const access = ai.data?.enabled ? ai.data.apps.find((a) => a.app === app && a.enabled) : undefined;
  if (!access) return null;
  return (
    <Link
      href={projectHref(project, "ai")}
      title={t("title", { budget: access.daily_token_budget })}
      className="inline-flex h-5 items-center gap-1 rounded-full bg-gradient-to-r from-violet-500/15 to-primary/15 px-2 text-xs font-medium text-violet-800 transition-colors hover:from-violet-500/25 hover:to-primary/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-violet-200"
    >
      <Sparkles className="size-3" aria-hidden />
      {t("label")}
    </Link>
  );
}
