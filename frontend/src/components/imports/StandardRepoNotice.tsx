"use client";

import { ExternalLink, GitPullRequest } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import type { ImportSourceView } from "@/types";

/**
 * Where the import stands on the template model: until its pull request is
 * merged the apps run from the platform's copy, once merged deploy/ in the
 * repository drives them.
 */
export function StandardRepoNotice({
  source,
  canManage,
  onOpen,
}: {
  source: ImportSourceView;
  canManage: boolean;
  onOpen: () => void;
}) {
  const t = useTranslations("imports.list.standard");
  const pr = source.standard_repo;
  const deployed = source.services.some((s) => s.status === "running");
  if (!pr && !deployed) return null;
  const switched = pr?.state === "switched";
  const reopen = !pr || pr.state === "closed" || pr.state === "failed";
  return (
    <div
      role="status"
      className={`space-y-1 rounded-xl border px-3 py-2 text-sm ${
        switched ? "border-emerald-500/40 bg-emerald-500/10" : "border-sky-500/40 bg-sky-500/10"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <GitPullRequest className="h-4 w-4" aria-hidden />
        <span className="flex-1">
          {t(`state.${pr?.state ?? "none"}`, { repo: source.repo_full_name })}
        </span>
        {pr?.url && (
          <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
            {t("view", { number: pr.number ?? "" })}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        )}
        {canManage && reopen && (
          <Button size="sm" variant="outline" onClick={onOpen}>
            {t("open")}
          </Button>
        )}
      </div>
      {pr?.state === "open" && <p className="text-xs text-muted-foreground">{t("untilMerged")}</p>}
      {pr?.error && <p className="text-xs text-destructive">{pr.error}</p>}
      {!!pr?.conflicts?.length && (
        <ul className="list-disc pl-5 text-xs">
          {pr.conflicts.map((c) => (
            <li key={c.path}>
              <span className="font-mono">{c.path}</span>: {t(`conflict.${c.kind}`)}
            </li>
          ))}
        </ul>
      )}
      {Object.entries(pr?.excluded ?? {}).map(([app, why]) => (
        <p key={app} className="text-xs text-amber-700 dark:text-amber-400">
          <span className="font-mono">{app}</span>: {why}
        </p>
      ))}
      {!!pr?.kept?.length && (
        <p className="text-xs text-muted-foreground">{t("kept", { apps: pr.kept.join(", ") })}</p>
      )}
    </div>
  );
}
