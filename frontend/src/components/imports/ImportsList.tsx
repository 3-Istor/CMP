"use client";

import { GitBranch, GitPullRequestArrow, RotateCw, Trash2, TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Switch } from "@/components/ui/switch";
import {
  deleteImport,
  getImports,
  openStandardPr,
  pullImportOrigin,
  rebuildImport,
  updateImportSettings,
} from "@/lib/api";
import { importRefusal } from "@/lib/importRefusal";
import type { ImportSourceView } from "@/types";
import { RebuildPendingNotice } from "./RebuildPendingNotice";
import { StandardRepoNotice } from "./StandardRepoNotice";
import { pollWhileVisible } from "@/lib/visiblePolling";

const TH = "px-3 py-2 text-left text-xs font-medium text-muted-foreground";
const TD = "px-3 py-2 align-middle";
const ACTIVE = ["queued", "claimed", "running"];

function lastSha(source: ImportSourceView): string | null {
  const built = source.services.find((s) => s.last_sha)?.last_sha;
  return (built ?? source.runs.at(-1)?.sha ?? null)?.slice(0, 12) ?? null;
}

/** The project's imports; nothing at all when the feature is off or empty. */
export function ImportsList({
  projectName,
  canManage,
}: {
  projectName: string;
  canManage: boolean;
}) {
  const t = useTranslations("imports.list");
  const router = useRouter();
  const [sources, setSources] = useState<ImportSourceView[]>([]);
  const [deleting, setDeleting] = useState<ImportSourceView | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getImports(projectName)
      .then(setSources)
      .catch(() => setSources([]));
  }, [projectName]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const busy = sources.some((s) => ACTIVE.includes(s.runs.at(-1)?.status ?? ""));
  useEffect(() => {
    if (!busy) return;
    return pollWhileVisible(refresh, 10_000);
  }, [busy, refresh]);

  if (sources.length === 0) return null;
  const base = `/projects/${encodeURIComponent(projectName)}`;

  const rebuild = async (source: ImportSourceView) => {
    try {
      await rebuildImport(projectName, source.id);
      toast.success(t("rebuildStarted", { repo: source.repo_full_name }));
      refresh();
    } catch (err) {
      const { message, latestRun } = importRefusal(err);
      toast.error(t("rebuildFailed", { message }), {
        action: latestRun
          ? { label: t("openLatestRun"), onClick: () => router.push(latestRun.ui_path) }
          : undefined,
      });
    }
  };

  const setAutoRebuild = async (source: ImportSourceView, value: boolean) => {
    setSaving(source.id);
    try {
      const updated = await updateImportSettings(projectName, source.id, { auto_rebuild: value });
      setSources((all) => all.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      toast.error(t("autoRebuildFailed", { message: err instanceof Error ? err.message : String(err) }));
    } finally {
      setSaving(null);
    }
  };

  const openPr = async (source: ImportSourceView) => {
    try {
      const updated = await openStandardPr(projectName, source.id);
      setSources((all) => all.map((s) => (s.id === updated.id ? updated : s)));
      toast.success(t("standard.opened", { repo: source.repo_full_name }));
    } catch (err) {
      toast.error(t("standard.openFailed", { message: importRefusal(err).message }));
    }
  };

  const pullOrigin = async (source: ImportSourceView) => {
    try {
      await pullImportOrigin(projectName, source.id);
      toast.success(t("pullStarted", { origin: source.origin_repo_full_name ?? "" }));
      refresh();
    } catch (err) {
      toast.error(t("pullFailed", { message: importRefusal(err).message }));
    }
  };

  const pending = sources.filter((s) => s.rebuild_pending);
  const leaking = sources.filter((s) => (s.copy?.leaks_total ?? 0) > 0);

  return (
    <section id="imports" aria-labelledby="imports-title" className="scroll-mt-40 space-y-2">
      <h2 id="imports-title" className="text-sm font-semibold">
        {t("title")}
      </h2>
      {pending.map((source) => (
        <RebuildPendingNotice
          key={source.id}
          source={source}
          canManage={canManage}
          onRebuild={() => void rebuild(source)}
        />
      ))}
      {leaking.map((source) => (
        <p
          key={`leaks-${source.id}`}
          role="status"
          className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
          {t("copyLeaks", { count: source.copy?.leaks_total ?? 0, repo: source.repo_full_name })}
        </p>
      ))}
      {sources.map((source) => (
        <StandardRepoNotice
          key={`standard-${source.id}`}
          source={source}
          canManage={canManage}
          onOpen={() => void openPr(source)}
        />
      ))}
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/40">
            <tr>
              <th scope="col" className={TH}>{t("repo")}</th>
              <th scope="col" className={TH}>{t("branch")}</th>
              <th scope="col" className={TH}>{t("sha")}</th>
              <th scope="col" className={TH}>{t("status")}</th>
              <th scope="col" className={TH}>{t("apps")}</th>
              <th scope="col" className={TH}>{t("autoRebuild")}</th>
              {canManage && <th scope="col" className={TH}>{t("actions")}</th>}
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => {
              const run = source.runs.at(-1);
              const wizardRun = source.runs.filter((r) => r.kind !== "sync").at(-1);
              const active = ACTIVE.includes(run?.status ?? "");
              return (
                <tr key={source.id} className="border-b last:border-0">
                  <th scope="row" className={`${TD} text-left font-mono font-normal`}>
                    {wizardRun && canManage ? (
                      <Link href={`${base}/import?run=${encodeURIComponent(wizardRun.id)}`} className="text-primary hover:underline">
                        {source.repo_full_name}
                      </Link>
                    ) : (
                      source.repo_full_name
                    )}
                    {source.source_mode === "copy" && source.origin_repo_full_name && (
                      <span className="block font-sans text-xs text-muted-foreground">
                        {t("copiedFrom", { origin: source.origin_repo_full_name })}
                      </span>
                    )}
                  </th>
                  <td className={TD}>
                    <span className="inline-flex items-center gap-1 font-mono text-xs">
                      <GitBranch className="h-3 w-3" aria-hidden />
                      {source.branch}
                    </span>
                  </td>
                  <td className={`${TD} font-mono text-xs`}>{lastSha(source) ?? "-"}</td>
                  <td className={TD}>
                    {run ? t(`runStatus.${run.kind}.${run.status}`) : t("never")}
                  </td>
                  <td className={TD}>{source.services.length}</td>
                  <td className={TD}>
                    <Switch
                      checked={source.auto_rebuild}
                      disabled={!canManage || saving === source.id || source.standard_repo?.state === "switched"}
                      onCheckedChange={(value) => void setAutoRebuild(source, value)}
                      aria-label={t("autoRebuildOf", { repo: source.repo_full_name })}
                    />
                  </td>
                  {canManage && (
                    <td className={TD}>
                      <div className="flex flex-wrap gap-1">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={
                            active ||
                            source.services.length === 0 ||
                            ["merged", "switched"].includes(source.standard_repo?.state ?? "")
                          }
                          onClick={() => void rebuild(source)}
                          aria-label={t("rebuildOf", { repo: source.repo_full_name })}
                        >
                          <RotateCw aria-hidden />
                          {t("rebuild")}
                        </Button>
                        {source.source_mode === "copy" && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={active || source.copy?.state !== "copied"}
                            onClick={() => void pullOrigin(source)}
                            aria-label={t("pullOf", { origin: source.origin_repo_full_name ?? "" })}
                          >
                            <GitPullRequestArrow aria-hidden />
                            {t("pull")}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={active}
                          onClick={() => setDeleting(source)}
                          aria-label={t("deleteOf", { repo: source.repo_full_name })}
                        >
                          <Trash2 aria-hidden />
                          {t("delete")}
                        </Button>
                      </div>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        level={2}
        title={t("deleteTitle", { repo: deleting?.repo_full_name ?? "" })}
        description={t("deleteDescription")}
        consequences={[t("deleteApps", { count: deleting?.services.length ?? 0 }), t("deleteData"), t("deleteLinks")]}
        expectedName={deleting?.repo_full_name}
        confirmLabel={t("deleteConfirm")}
        destructive
        onConfirm={async () => {
          if (!deleting) return;
          await deleteImport(projectName, deleting.id);
          toast.success(t("deleteStarted"));
          refresh();
        }}
      />
    </section>
  );
}
