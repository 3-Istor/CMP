"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getSecurityBackups,
  requestSecurityBackup,
  type SecurityBackup,
} from "@/lib/api";
import { Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage, relativeTime } from "./labels";

const PHASES: Record<string, string> = {
  completed: "Réussie",
  failed: "En échec",
  running: "En cours",
  started: "En cours",
  pending: "En attente",
};

/** Backups of one app's databases, with an on-demand backup. */
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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () =>
      getSecurityBackups(project, app).then(
        (rows) => {
          setBackups(rows);
          setError(null);
        },
        (e) => setError(errorMessage(e)),
      ),
    [project, app],
  );

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load]);

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
      <CardContent className="text-sm">
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="pt-3 text-xs text-muted-foreground">
          La restauration arrive dans une prochaine version : elle se fera dans une
          base à côté, puis par une bascule confirmée.
        </p>
      </CardContent>
    </Card>
  );
}
