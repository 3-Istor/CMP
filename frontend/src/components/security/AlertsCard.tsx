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
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./labels";

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
  const t = useTranslations("security");
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
        toast.success(t("alerts.testSent"));
      } else {
        setTargets(
          await setSecurityAlertTarget(project, app, action === "save" ? url : null),
        );
        setUrl("");
        toast.success(t(action === "save" ? "alerts.saved" : "alerts.removed"));
      }
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (!targets) return null;
  const own = app ? targets.app : targets.project;
  const scope = t(app ? "alerts.scope.app" : "alerts.scope.project");

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("alerts.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          {t("alerts.description", {
            target: targets.effective
              ? t(`alerts.origin.${targets.effective}`)
              : t("alerts.none"),
          })}
        </p>
        {own?.configured && (
          <p>
            {t("alerts.ownWebhook", { scope })}{" "}
            <span className="font-mono text-xs">{own.hint}</span>
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
                {t(own?.configured ? "alerts.replace" : "alerts.useThis")}
              </Button>
              {own?.configured && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy !== null}
                  onClick={() => run("remove")}
                >
                  {t("alerts.remove")}
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
                  {t("alerts.sendTest")}
                </Button>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {t("alerts.storedInVault")}{" "}
              {app ? t("alerts.fallbackToProject") : t("alerts.appCanOwn")}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
