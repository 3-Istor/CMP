"use client";

import { GitBranch, RotateCw, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Switch } from "@/components/ui/switch";
import { deleteImport, getImports, openStandardPr, rebuildImport, updateImportSettings } from "@/lib/api";
import { importRefusal } from "@/lib/importRefusal";
import type { ImportSourceView } from "@/types";
import { RebuildPendingNotice } from "./RebuildPendingNotice";
import { StandardRepoNotice } from "./StandardRepoNotice";
import { pollWhileVisible } from "@/lib/visiblePolling";

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

  const pending = sources.filter((s) => s.rebuild_pending);

  const columns: DataColumn<ImportSourceView>[] = [
    {
      key: "repo_full_name",
      header: t("repo"),
      sortable: true,
      className: "font-mono",
      cell: (source) => {
        const run = source.runs.at(-1);
        return run && canManage ? (
          <Link href={`${base}/import?run=${encodeURIComponent(run.id)}`} className="text-primary hover:underline">
            {source.repo_full_name}
          </Link>
        ) : (
          source.repo_full_name
        );
      },
    },
    {
      key: "branch",
      header: t("branch"),
      sortable: true,
      cell: (source) => (
        <span className="inline-flex items-center gap-1 font-mono text-xs">
          <GitBranch className="h-3 w-3" aria-hidden />
          {source.branch}
        </span>
      ),
    },
    {
      key: "sha",
      header: t("sha"),
      className: "font-mono text-xs",
      cell: (source) => lastSha(source) ?? "-",
    },
    {
      key: "status",
      header: t("status"),
      cell: (source) => {
        const run = source.runs.at(-1);
        return run ? t(`runStatus.${run.kind}.${run.status}`) : t("never");
      },
    },
    {
      key: "apps",
      header: t("apps"),
      sortable: true,
      sortValue: (source) => source.services.length,
      cell: (source) => source.services.length,
    },
    {
      key: "auto_rebuild",
      header: t("autoRebuild"),
      cell: (source) => (
        <Switch
          checked={source.auto_rebuild}
          disabled={!canManage || saving === source.id || source.standard_repo?.state === "switched"}
          onCheckedChange={(value) => void setAutoRebuild(source, value)}
          aria-label={t("autoRebuildOf", { repo: source.repo_full_name })}
        />
      ),
    },
    ...(canManage
      ? [
          {
            key: "actions",
            header: t("actions"),
            cell: (source: ImportSourceView) => {
              const active = ACTIVE.includes(source.runs.at(-1)?.status ?? "");
              return (
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
              );
            },
          },
        ]
      : []),
  ];

  return (
    <section aria-labelledby="imports-title" className="space-y-2">
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
      {sources.map((source) => (
        <StandardRepoNotice
          key={`standard-${source.id}`}
          source={source}
          canManage={canManage}
          onOpen={() => void openPr(source)}
        />
      ))}
      <DataTable
        columns={columns}
        rows={sources}
        rowKey={(source) => source.id}
        storageKey="imports.list"
      />
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
