"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  listProjectExports,
  projectExportLink,
  requestProjectExport,
  type ProjectExport,
} from "@/lib/api";
import { Download, Loader2, PackageOpen } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { pollWhileVisible } from "@/lib/visiblePolling";

const ACTIVE = ["pending", "running"];
const DOWNLOADABLE = ["ready", "partial"];
const POLL_MS = 10_000;

const SIZE_UNITS = ["byte", "kilobyte", "megabyte", "gigabyte"];

function formatSize(bytes: number, locale: string): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit: SIZE_UNITS[unit],
    maximumFractionDigits: 1,
  }).format(value);
}

export function ProjectExportCard({ project }: { project: string }) {
  const t = useTranslations("projectSettings.export");
  const locale = useLocale();
  const [exports, setExports] = useState<ProjectExport[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);

  const load = useCallback(() => {
    listProjectExports(project)
      .then((rows) => {
        setExports(rows);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, [project]);

  useEffect(load, [load]);

  const running = exports?.some((e) => ACTIVE.includes(e.status)) ?? false;
  useEffect(() => {
    if (!running) return;
    return pollWhileVisible(load, POLL_MS);
  }, [running, load]);

  const start = async () => {
    await requestProjectExport(project);
    toast.success(t("started"));
    load();
  };

  const download = async (row: ProjectExport) => {
    setDownloading(row.id);
    try {
      const link = await projectExportLink(project, row.id);
      window.location.assign(link.url);
    } catch (err) {
      toast.error(t("linkFailed", { message: String(err) }));
    } finally {
      setDownloading(null);
    }
  };

  const date = (iso: string | null) =>
    iso
      ? new Date(iso).toLocaleDateString(locale, {
          day: "numeric",
          month: "short",
          year: "numeric",
        })
      : "";

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-medium">{t("title")}</p>
            <p className="mt-0.5 text-sm text-muted-foreground">{t("hint")}</p>
          </div>
          <Button
            variant="outline"
            disabled={running || exports === null}
            onClick={() => setConfirming(true)}
          >
            {running ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <PackageOpen aria-hidden />
            )}
            {running ? t("running") : t("start")}
          </Button>
        </div>

        {failed && (
          <p className="text-sm text-destructive">{t("loadFailed")}</p>
        )}
        {exports && exports.length > 0 && (
          <ul className="divide-y rounded-md border text-sm">
            {exports.map((row) => {
              const canDownload =
                DOWNLOADABLE.includes(row.status) &&
                row.expires_at !== null &&
                new Date(row.expires_at) > new Date();
              return (
                <li
                  key={row.id}
                  className="flex flex-wrap items-center justify-between gap-3 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p>
                      {date(row.created_at)} · {t(`status.${row.status}`)}
                      {row.size !== null && ` · ${formatSize(row.size, locale)}`}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {row.requested_by}
                      {row.expires_at &&
                        ` · ${t("availableUntil", { date: date(row.expires_at) })}`}
                      {row.problems.length > 0 &&
                        ` · ${t("problems", { count: row.problems.length })}`}
                    </p>
                  </div>
                  {canDownload && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={downloading === row.id}
                      onClick={() => void download(row)}
                    >
                      {downloading === row.id ? (
                        <Loader2 className="animate-spin" aria-hidden />
                      ) : (
                        <Download aria-hidden />
                      )}
                      {t("download")}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        level={1}
        title={t("confirm.title", { name: project })}
        description={t("confirm.description")}
        confirmLabel={t("confirm.confirm")}
        onConfirm={start}
      />
    </Card>
  );
}
