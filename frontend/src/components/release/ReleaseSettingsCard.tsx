"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { getReleaseSettings, updateReleaseSettings } from "@/lib/api";
import type { ReleaseSettings, ReleaseSettingsRead } from "@/types";
import { Loader2, Save, SlidersHorizontal } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { errorMessage } from "./format";

const STRATEGY_OPTIONS: ReleaseSettings["strategy"][] = ["rolling", "canary"];
const PRESET_OPTIONS: ReleaseSettings["preset"][] = ["safe", "fast", "demo", "manual"];
const STICKINESS_OPTIONS: ReleaseSettings["stickiness"][] = ["cookie", "none"];

function componentOf(file: string): string {
  return file.match(/values-(\w+)\.yaml$/)?.[1] ?? file;
}

const code = (chunks: ReactNode) => <code className="font-mono">{chunks}</code>;

export function ReleaseSettingsCard({
  deploymentId,
  inProgress,
}: {
  deploymentId: number;
  inProgress: boolean;
}) {
  const t = useTranslations("release");
  const [remote, setRemote] = useState<ReleaseSettingsRead | null>(null);
  const [draft, setDraft] = useState<ReleaseSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [blockerOpen, setBlockerOpen] = useState(false);
  const [migrationsCommand, setMigrationsCommand] = useState("");
  const [pendingMigrations, setPendingMigrations] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getReleaseSettings(deploymentId);
      setRemote(data);
      setDraft(data.settings);
      setPendingMigrations(null);
      setMigrationsCommand((data.migrationsCommand ?? ["/app/migrate"]).join(" "));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [deploymentId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          {t("settings.loadFailed", { error })}
        </CardContent>
      </Card>
    );
  }
  if (!remote || !draft) return <Skeleton className="h-48 w-full" />;

  const locked = !remote.canEdit || inProgress || saving;
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(remote.settings) || pendingMigrations !== null;
  const blocker = remote.canaryBlocker;

  const chooseStrategy = (strategy: ReleaseSettings["strategy"]) => {
    if (strategy === "canary" && blocker && !pendingMigrations) {
      setBlockerOpen(true);
      return;
    }
    if (strategy === "rolling") setPendingMigrations(null);
    set("strategy", strategy);
  };

  const enableMigrationsAndCanary = () => {
    const command = migrationsCommand.trim().split(/\s+/).filter(Boolean);
    if (command.length === 0) return;
    setPendingMigrations(command);
    set("strategy", "canary");
    setBlockerOpen(false);
  };
  const set = <K extends keyof ReleaseSettings>(key: K, value: ReleaseSettings[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  const save = async () => {
    setSaving(true);
    try {
      const saved = await updateReleaseSettings(
        deploymentId,
        draft,
        draft.strategy === "canary" ? pendingMigrations : null,
      );
      const kept = Object.entries(saved.strategies)
        .filter(([, s]) => s !== draft.strategy)
        .map(([file]) => componentOf(file));
      toast.success(
        Object.keys(saved.commits).length === 0
          ? t("settings.upToDate")
          : t("settings.committed"),
        { duration: 7000 },
      );
      if (kept.length > 0) {
        toast.info(t("settings.keptRolling", { component: kept.join(", ") }), {
          duration: 9000,
        });
      }
      await load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
          {t("settings.title")}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {t.rich("settings.savedIn", { code, files: remote.files.join(", ") })}
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {!remote.declared && (
          <p className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
            {t.rich("settings.noSection", { code })}
          </p>
        )}

        <div className="space-y-1.5">
          <Label>{t("settings.strategy")}</Label>
          <Select
            value={draft.strategy}
            onValueChange={(v) => chooseStrategy(v as ReleaseSettings["strategy"])}
            disabled={locked}
          >
            <SelectTrigger className="w-full">
              <SelectValue>
                {(v) => t(`strategies.${v as ReleaseSettings["strategy"]}.label`)}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {STRATEGY_OPTIONS.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`strategies.${value}.label`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {t(`strategies.${draft.strategy}.hint`)}
          </p>
          {draft.strategy === "canary" && remote.files.length > 1 && (
            <p className="text-xs text-muted-foreground">
              {t.rich("settings.fullstackCanary", { code })}
            </p>
          )}
          {blocker && draft.strategy === "rolling" && (
            <p className="text-xs text-muted-foreground">
              {t("settings.canaryUnavailable", { reason: t(`blockers.${blocker.code}.short`) })}
            </p>
          )}
          {pendingMigrations && draft.strategy === "canary" && (
            <p className="text-xs text-muted-foreground">
              {t.rich("settings.migrationsWithCommand", {
                code,
                command: pendingMigrations.join(" "),
              })}
            </p>
          )}
        </div>

        {draft.strategy === "canary" && (
          <>
            <div className="space-y-1.5">
              <Label>{t("settings.preset")}</Label>
              <Select
                value={draft.preset}
                onValueChange={(v) => set("preset", v as ReleaseSettings["preset"])}
                disabled={locked}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>
                    {(v) => t(`presets.${v as ReleaseSettings["preset"]}.label`)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {PRESET_OPTIONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`presets.${value}.label`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {t("settings.smokeThen", { steps: t(`presets.${draft.preset}.steps`) })}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>{t("settings.stickiness")}</Label>
              <Select
                value={draft.stickiness}
                onValueChange={(v) => set("stickiness", v as ReleaseSettings["stickiness"])}
                disabled={locked}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>
                    {(v) => t(`stickiness.${v as ReleaseSettings["stickiness"]}`)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {STICKINESS_OPTIONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`stickiness.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="release-smoke">{t("settings.smokePath")}</Label>
                <Input
                  id="release-smoke"
                  value={draft.smokePath}
                  onChange={(e) => set("smokePath", e.target.value)}
                  disabled={locked}
                  className="font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="release-error-rate">{t("settings.errorRate")}</Label>
                <Input
                  id="release-error-rate"
                  type="number"
                  min={0.1}
                  max={100}
                  step={0.1}
                  value={draft.errorRateMax}
                  onChange={(e) => set("errorRateMax", Number(e.target.value))}
                  disabled={locked}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="release-min-requests">{t("settings.minRequests")}</Label>
                <Input
                  id="release-min-requests"
                  type="number"
                  min={1}
                  step={1}
                  value={draft.minRequests}
                  onChange={(e) => set("minRequests", Number(e.target.value))}
                  disabled={locked}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{t("settings.inconclusiveNote")}</p>

            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="release-allow-force">{t("settings.allowForce")}</Label>
                <p className="text-xs text-muted-foreground">{t("settings.allowForceHint")}</p>
              </div>
              <Switch
                id="release-allow-force"
                checked={draft.allowForce}
                onCheckedChange={(checked) => set("allowForce", checked)}
                disabled={locked}
              />
            </div>
          </>
        )}

        {inProgress && (
          <p className="text-xs text-muted-foreground">{t("settings.inProgress")}</p>
        )}
        {!remote.canEdit ? (
          <p className="border-t border-border pt-2 text-xs text-muted-foreground">
            {t("settings.adminOnly")}
          </p>
        ) : (
          dirty &&
          !inProgress && (
            <div className="border-t border-border pt-2">
              <Button onClick={save} disabled={saving} className="w-full">
                {saving ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                {t("settings.save")}
              </Button>
            </div>
          )
        )}
      </CardContent>

      {blocker && (
        <Dialog open={blockerOpen} onOpenChange={setBlockerOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{t(`blockers.${blocker.code}.title`)}</DialogTitle>
              <DialogDescription>{t(`blockers.${blocker.code}.why`)}</DialogDescription>
            </DialogHeader>
            {blocker.code === "db_without_migrations" ? (
              <div className="space-y-3 text-sm">
                <div className="space-y-1.5">
                  <Label htmlFor="release-migrations-command">
                    {t("migrations.commandLabel")}
                  </Label>
                  <Input
                    id="release-migrations-command"
                    value={migrationsCommand}
                    onChange={(e) => setMigrationsCommand(e.target.value)}
                    className="font-mono"
                  />
                  <p className="text-xs text-muted-foreground">
                    {t.rich("migrations.commandHint", { code })}
                  </p>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("migrations.databaseHint")}
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t(`blockers.${blocker.code}.fix`)}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setBlockerOpen(false)}>
                {blocker.code === "db_without_migrations"
                  ? t("dialog.cancel")
                  : t("dialog.close")}
              </Button>
              {blocker.code === "db_without_migrations" && (
                <Button
                  onClick={enableMigrationsAndCanary}
                  disabled={migrationsCommand.trim() === ""}
                >
                  {t("migrations.enable")}
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}
