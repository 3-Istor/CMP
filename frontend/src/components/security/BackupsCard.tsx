"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getSecurityBackups,
  getSecurityDatabases,
  requestSecurityBackup,
  type SecurityBackup,
  type SecurityDatabase,
} from "@/lib/api";
import { Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage, relativeTime } from "./labels";
import { RestoreDialog } from "./RestoreDialog";

const POLL_WHILE_BUSY_MS = 15_000;

const PHASES: Record<string, string> = {
  completed: "Réussie",
  failed: "En échec",
  running: "En cours",
  started: "En cours",
  pending: "En attente",
};

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
  const [backups, setBackups] = useState<SecurityBackup[] | null>(null);
  const [databases, setDatabases] = useState<SecurityDatabase[]>([]);
  const [error, setError] = useState<string | null>(null);
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
        (e) => setError(errorMessage(e)),
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
      toast.success(`Sauvegarde lancée : ${started.join(", ")}`);
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
        <CardTitle>Sauvegardes</CardTitle>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={load} title="Rafraîchir">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          {canAdmin && (
            <Button size="sm" variant="outline" onClick={backupNow} disabled={busy}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Sauvegarder maintenant
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
                    ? `${d.ready_instances}/${d.instances} instance${d.instances > 1 ? "s" : ""} prête${d.instances > 1 ? "s" : ""}`
                    : `${d.restored_from ? "Restauration en cours" : "En préparation"} : ${d.phase}`}
                </span>
                {d.restored_from && (
                  <span className="text-xs text-muted-foreground">
                    restaurée depuis {d.restored_from}
                    {d.restored_to
                      ? `, jusqu'au ${new Date(d.restored_to).toLocaleString("fr-FR")}`
                      : `, sauvegarde ${d.restored_backup}`}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {error ? (
          <p className="text-destructive">{error}</p>
        ) : backups === null ? (
          <p className="text-muted-foreground">Chargement…</p>
        ) : backups.length === 0 ? (
          <p className="text-muted-foreground">
            Aucune sauvegarde. Active-les dans l&apos;onglet Application, panneau
            « Exposition et données ».
          </p>
        ) : (
          <table className="w-full">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="pb-2 font-medium">Base</th>
                <th className="pb-2 font-medium">Quand</th>
                <th className="pb-2 font-medium">État</th>
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
                        manuelle
                      </Badge>
                    )}
                  </td>
                  <td className="py-2 text-muted-foreground">
                    {relativeTime(b.stopped_at ?? b.started_at)}
                  </td>
                  <td className="py-2">
                    <span className={b.phase === "failed" ? "text-destructive" : ""}>
                      {PHASES[b.phase] ?? b.phase}
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
                          Restaurer
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
