"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  getSecurityAlertTargets,
  type SecurityAlertTargets,
  setSecurityAlertTarget,
  testSecurityAlertTarget,
} from "@/lib/api";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./labels";

const ORIGIN_LABELS = {
  app: "le webhook de cette app",
  project: "le webhook du projet",
  platform: "le canal par défaut de la plateforme",
} as const;

/** Where new core findings are sent, and the team's own Discord webhook. */
export function AlertsCard({
  project,
  app,
  canAdmin,
}: {
  project: string;
  app: string | null;
  canAdmin: boolean;
}) {
  const [targets, setTargets] = useState<SecurityAlertTargets | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<"save" | "remove" | "test" | null>(null);

  useEffect(() => {
    getSecurityAlertTargets(project, app).then(setTargets, () => setTargets(null));
  }, [project, app]);

  async function run(action: "save" | "remove" | "test") {
    setBusy(action);
    try {
      if (action === "test") {
        await testSecurityAlertTarget(project, app);
        toast.success("Message de test envoyé");
      } else {
        setTargets(
          await setSecurityAlertTarget(project, app, action === "save" ? url : null),
        );
        setUrl("");
        toast.success(action === "save" ? "Webhook enregistré" : "Webhook retiré");
      }
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (!targets) return null;
  const own = app ? targets.app : targets.project;
  const scope = app ? "cette app" : "ce projet";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Alertes Discord</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Chaque nouveau problème du socle est envoyé une fois, vers{" "}
          {targets.effective ? ORIGIN_LABELS[targets.effective] : "aucun canal"}.
        </p>
        {own?.configured && (
          <p>
            Webhook de {scope} : <span className="font-mono text-xs">{own.hint}</span>
          </p>
        )}
        {canAdmin && (
          <div className="space-y-2">
            <Input
              type="url"
              placeholder="https://discord.com/api/webhooks/…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              autoComplete="off"
            />
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={!url || busy !== null} onClick={() => run("save")}>
                {busy === "save" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {own?.configured ? "Remplacer" : "Utiliser ce webhook"}
              </Button>
              {own?.configured && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy !== null}
                  onClick={() => run("remove")}
                >
                  Retirer
                </Button>
              )}
              {targets.effective && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy !== null}
                  onClick={() => run("test")}
                >
                  {busy === "test" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Envoyer un test
                </Button>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Le webhook est stocké dans Vault et n&apos;est plus jamais affiché.
              {app
                ? " Sans webhook d'app, celui du projet est utilisé."
                : " Une app peut avoir le sien."}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
