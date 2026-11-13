"use client";

import { GitBranch, RotateCw, ShieldAlert, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Switch } from "@/components/ui/switch";
import { deleteImport, getImports, rebuildImport, updateImportSettings } from "@/lib/api";
import type { ImportSourceView } from "@/types";

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
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  if (sources.length === 0) return null;
  const base = `/projects/${encodeURIComponent(projectName)}`;

  const rebuild = async (source: ImportSourceView) => {
    try {
      await rebuildImport(projectName, source.id);
      toast.success(t("rebuildStarted", { repo: source.repo_full_name }));
      refresh();
    } catch (err) {
      toast.error(t("rebuildFailed", { message: err instanceof Error ? err.message : String(err) }));
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

  const pending = sources.filter((s) => s.rebuild_pending);

  return (
    <section aria-labelledby="imports-title" className="space-y-2">
      <h2 id="imports-title" className="text-sm font-semibold">
        {t("title")}
      </h2>
      {pending.map((source) => (
        <div
          key={source.id}
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
        >
          <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400" aria-hidden />
          <span className="flex-1">
            {t("pending", {
              repo: source.repo_full_name,
              reason: t(`pendingReason.${source.rebuild_pending?.reason ?? "not_admin"}`, {
                user: source.rebuild_pending?.user ?? "",
              }),
            })}
          </span>
          {canManage && (
            <Button size="sm" variant="outline" onClick={() => void rebuild(source)}>
              <RotateCw aria-hidden />
              {t("confirmRebuild")}
            </Button>
          )}
        </div>
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
              const active = ACTIVE.includes(run?.status ?? "");
              return (
                <tr key={source.id} className="border-b last:border-0">
                  <th scope="row" className={`${TD} text-left font-mono font-normal`}>
                    {run && canManage ? (
                      <Link href={`${base}/import?run=${encodeURIComponent(run.id)}`} className="text-primary hover:underline">
                        {source.repo_full_name}
                      </Link>
                    ) : (
                      source.repo_full_name
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
                      disabled={!canManage || saving === source.id}
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
                          disabled={active || source.services.length === 0}
                          onClick={() => void rebuild(source)}
                          aria-label={t("rebuildOf", { repo: source.repo_full_name })}
                        >
                          <RotateCw aria-hidden />
                          {t("rebuild")}
                        </Button>
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
