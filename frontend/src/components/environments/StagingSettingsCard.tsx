"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  disableStaging,
  enableStaging,
  getStagingOffhours,
  updateStagingOffhours,
} from "@/lib/api";
import type { StagingOffhours, StagingStatus } from "@/types";
import { Loader2, Moon, Save } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage, TONE } from "../release/format";

export const DEFAULT_OFFHOURS: StagingOffhours = {
  enabled: true,
  sleepAt: "0 19 * * 1-5",
  wakeAt: "0 8 * * 1-5",
  timezone: "Europe/Paris",
};

function OffhoursFields({
  value,
  onChange,
  disabled,
}: {
  value: StagingOffhours;
  onChange: (next: StagingOffhours) => void;
  disabled: boolean;
}) {
  const t = useTranslations("environments");
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-4">
        <div>
          <Label htmlFor="staging-offhours">{t("offhours.label")}</Label>
          <p className="text-xs text-muted-foreground">{t("offhours.hint")}</p>
        </div>
        <Switch
          id="staging-offhours"
          checked={value.enabled}
          onCheckedChange={(enabled) => onChange({ ...value, enabled })}
          disabled={disabled}
        />
      </div>
      {value.enabled && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="staging-sleep">{t("offhours.sleepCron")}</Label>
            <Input
              id="staging-sleep"
              className="font-mono"
              value={value.sleepAt}
              onChange={(e) => onChange({ ...value, sleepAt: e.target.value })}
              disabled={disabled}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="staging-wake">{t("offhours.wakeCron")}</Label>
            <Input
              id="staging-wake"
              className="font-mono"
              value={value.wakeAt}
              onChange={(e) => onChange({ ...value, wakeAt: e.target.value })}
              disabled={disabled}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="staging-tz">{t("offhours.timezone")}</Label>
            <Input
              id="staging-tz"
              value={value.timezone}
              onChange={(e) => onChange({ ...value, timezone: e.target.value })}
              disabled={disabled}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export function StagingSettingsCard({
  deploymentId,
  staging,
  canAdminister,
  onChanged,
}: {
  deploymentId: number;
  staging: StagingStatus | null;
  canAdminister: boolean;
  onChanged: () => void;
}) {
  const t = useTranslations("environments");
  const [offhours, setOffhours] = useState<StagingOffhours>(DEFAULT_OFFHOURS);
  const [busy, setBusy] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const ready = staging?.state === "ready";

  useEffect(() => {
    if (!ready) return;
    getStagingOffhours(deploymentId)
      .then(setOffhours)
      .catch((e) => toast.error(errorMessage(e)));
  }, [deploymentId, ready]);

  const act = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await action();
      toast.success(done);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
      setConfirmOff(false);
    }
  };

  if (!staging || staging.state === "failed") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("staging.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {staging?.state === "failed" && (
            <p className={`rounded-md border px-3 py-2 ${TONE.bad}`}>{staging.detail}</p>
          )}
          <p className="text-muted-foreground">
            {t.rich("staging.intro", {
              code: (chunks) => <code className="font-mono">{chunks}</code>,
            })}
          </p>
          <OffhoursFields value={offhours} onChange={setOffhours} disabled={!canAdminister || busy} />
          {canAdminister ? (
            <Button
              onClick={() => act(() => enableStaging(deploymentId, offhours), t("staging.enableStarted"))}
              disabled={busy}
            >
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("staging.enable")}
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">{t("staging.adminOnly")}</p>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Moon className="h-4 w-4 text-muted-foreground" />
          {t("staging.titleWithState", { state: t(`stagingState.${staging.state}`) })}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {staging.detail && <p className="text-muted-foreground">{staging.detail}</p>}
        {staging.state === "ready" && (
          <>
            <OffhoursFields value={offhours} onChange={setOffhours} disabled={!canAdminister || busy} />
            {canAdminister && (
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() =>
                    act(() => updateStagingOffhours(deploymentId, offhours), t("staging.scheduleSaved"))
                  }
                  disabled={busy}
                >
                  <Save className="mr-2 h-4 w-4" />
                  {t("staging.saveSchedule")}
                </Button>
                {confirmOff ? (
                  <Button
                    variant="destructive"
                    onClick={() => act(() => disableStaging(deploymentId), t("staging.deleteStarted"))}
                    disabled={busy}
                  >
                    {t("staging.confirmDelete")}
                  </Button>
                ) : (
                  <Button variant="ghost" onClick={() => setConfirmOff(true)} disabled={busy}>
                    {t("staging.disable")}
                  </Button>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
