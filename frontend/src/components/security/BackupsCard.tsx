"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NotConnected } from "@/components/clusters/NotConnected";
import {
  getSecurityBackups,
  getSecurityDatabases,
  isNotConnected,
  requestSecurityBackup,
  type SecurityBackup,
  type SecurityDatabase,
} from "@/lib/api";
import { Loader2, RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage, relativeTime } from "./labels";
import { RestoreDialog } from "./RestoreDialog";

const POLL_WHILE_BUSY_MS = 15_000;

const PHASE_KEYS = ["completed", "failed", "running", "started", "pending"];

/** One app's databases and their backups: back up now, or restore one. */
export function BackupsCard({
  project,
  app,
  canAdmin,
}: {
  project: string;
  app: string;
  canAdmin: boolean;
}) {
  const t = useTranslations("security");
  const locale = useLocale();
  const [backups, setBackups] = useState<SecurityBackup[] | null>(null);
  const [databases, setDatabases] = useState<SecurityDatabase[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState<SecurityBackup | null>(null);

  const load = useCallback(
    () =>
      Promise.all([getSecurityBackups(project, app), getSecurityDatabases(project, app)]).then(
        ([rows, clusters]) => {
          setBackups(rows);
          setDatabases(clusters);
          setError(null);
        },
        (e) => setError(e),
      ),
    [project, app],
  );

  const settling = databases.some((d) => !d.healthy);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const poll = settling ? setInterval(load, POLL_WHILE_BUSY_MS) : undefined;
    return () => {
      clearTimeout(first);
      clearInterval(poll);
    };
  }, [load, settling]);

  async function backupNow() {
    setBusy(true);
    try {
      const { backups: started } = await requestSecurityBackup(project, app);
      toast.success(t("common.backupStarted", { list: started.join(", ") }));
      await load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>{t("backups.title")}</CardTitle>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={load} title={t("backups.refresh")}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          {canAdmin && (
            <Button size="sm" variant="outline" onClick={backupNow} disabled={busy}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("finding.backupNow")}
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {databases.length > 0 && (
          <ul className="space-y-1.5">
            {databases.map((d) => (
              <li key={d.name} className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium">{d.name}</span>
                <span className={d.healthy ? "text-muted-foreground" : "text-amber-600"}>
                  {d.healthy
                    ? t("backups.instances", {
                        ready: d.ready_instances,
                        total: d.instances,
                      })
                    : t(d.restored_from ? "backups.restoring" : "backups.preparing", {
                        phase: d.phase,
                      })}
                </span>
                {d.restored_from && (
                  <span className="text-xs text-muted-foreground">
                    {d.restored_to
                      ? t("backups.restoredAt", {
                          source: d.restored_from,
                          date: new Date(d.restored_to).toLocaleString(locale),
                        })
                      : t("backups.restoredBackup", {
                          source: d.restored_from,
                          backup: d.restored_backup ?? "",
                        })}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {error != null ? (
          isNotConnected(error) ? (
            <NotConnected cloud={isNotConnected(error)!.cloud} />
          ) : (
            <p className="text-destructive">{errorMessage(error)}</p>
          )
        ) : backups === null ? (
          <p className="text-muted-foreground">{t("common.loading")}</p>
        ) : backups.length === 0 ? (
          <p className="text-muted-foreground">{t("backups.emptyState")}</p>
        ) : (
          <table className="w-full">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="pb-2 font-medium">{t("backups.database")}</th>
                <th className="pb-2 font-medium">{t("backups.when")}</th>
                <th className="pb-2 font-medium">{t("backups.status")}</th>
                {canAdmin && <th />}
              </tr>
            </thead>
            <tbody>
              {backups.slice(0, 20).map((b) => (
                <tr key={b.name} className="border-t align-top">
                  <td className="py-2">
                    {b.database}
                    {b.manual && (
                      <Badge variant="outline" className="ml-2 text-[11px]">
                        {t("backups.manual")}
                      </Badge>
                    )}
                  </td>
                  <td className="py-2 text-muted-foreground">
                    {relativeTime(b.stopped_at ?? b.started_at, locale) ??
                      t("common.never")}
                  </td>
                  <td className="py-2">
                    <span className={b.phase === "failed" ? "text-destructive" : ""}>
                      {PHASE_KEYS.includes(b.phase)
                        ? t(`backups.phase.${b.phase}`)
                        : b.phase}
                    </span>
                    {b.error && (
                      <span className="block text-xs text-muted-foreground">{b.error}</span>
                    )}
                  </td>
                  {canAdmin && (
                    <td className="py-2 text-right">
                      {b.restorable && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={settling}
                          onClick={() => setRestoring(b)}
                        >
                          {t("backups.restore")}
                        </Button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
      {restoring && (
        <RestoreDialog
          project={project}
          app={app}
          backup={restoring}
          onClose={() => setRestoring(null)}
          onRestored={load}
        />
      )}
    </Card>
  );
}
