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
import { Switch } from "@/components/ui/switch";
import {
  getSecurityPolicy,
  getSecuritySettings,
  type SecurityPolicy,
  updateSecurityPolicy,
  updateSecuritySettings,
} from "@/lib/api";
import { Loader2, Lock } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./labels";

type FailOn = "none" | "critical";

const FAIL_ON_VALUES: FailOn[] = ["none", "critical"];

const SELECT =
  "h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60 dark:bg-input/30";

const SETTINGS_SOURCES = ["platform", "project", "app"];

/** CI blocking of one app, as its admins see it. */
export function AppSettingsCard({
  project,
  app,
  canAdmin,
}: {
  project: string;
  app: string;
  canAdmin: boolean;
}) {
  const t = useTranslations("security");
  const [current, setCurrent] = useState<{
    value: FailOn;
    source: string;
    locked: boolean;
  } | null>(null);
  const [choice, setChoice] = useState<FailOn>("none");
  const [preview, setPreview] = useState<{ message: string; diff?: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSecuritySettings(project, app).then(
      (s) => {
        setCurrent(s.ci_fail_on);
        setChoice(s.ci_fail_on.value);
      },
      () => setCurrent(null),
    );
  }, [project, app]);

  async function run(dryRun: boolean) {
    setBusy(true);
    try {
      const result = await updateSecuritySettings(project, app, choice, dryRun);
      if (dryRun) {
        setPreview(result);
      } else {
        setPreview(null);
        setCurrent({ value: choice, source: "app", locked: false });
        toast.success(t("settings.saved"));
      }
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!current) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <label className="block space-y-1.5">
          <span className="font-medium">{t("settings.ciBlocking")}</span>
          <select
            className={SELECT}
            value={choice}
            disabled={!canAdmin || current.locked || busy}
            onChange={(e) => setChoice(e.target.value as FailOn)}
          >
            {FAIL_ON_VALUES.map((v) => (
              <option key={v} value={v}>
                {t(`failOn.${v}`)}
              </option>
            ))}
          </select>
        </label>
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {current.locked && <Lock className="h-3 w-3" />}
          {current.locked
            ? t("settings.enforced")
            : t("settings.current", {
                source: SETTINGS_SOURCES.includes(current.source)
                  ? t(`settingsSource.${current.source}`)
                  : current.source,
              })}
        </p>
        {canAdmin && !current.locked && choice !== current.value && (
          <Button size="sm" onClick={() => run(true)} disabled={busy}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t("common.save")}
          </Button>
        )}
      </CardContent>

      {preview && (
        <Dialog open onOpenChange={(open) => !open && setPreview(null)}>
          <DialogContent className="sm:max-w-xl">
            <DialogHeader>
              <DialogTitle>{t("settings.confirmChange")}</DialogTitle>
              <DialogDescription>{preview.message}</DialogDescription>
            </DialogHeader>
            <pre className="max-h-[50vh] overflow-auto rounded-md bg-muted p-3 font-mono text-[11px]">
              {preview.diff || t("common.noChange")}
            </pre>
            <DialogFooter>
              <Button variant="outline" onClick={() => setPreview(null)} disabled={busy}>
                {t("common.cancel")}
              </Button>
              <Button onClick={() => run(false)} disabled={busy}>
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("settings.commit")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}

/** Project default and lock, for CNP admins. */
export function ProjectPolicyCard({ project }: { project: string }) {
  const t = useTranslations("security");
  const [policy, setPolicy] = useState<SecurityPolicy | null>(null);
  const [value, setValue] = useState<FailOn>("none");
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSecurityPolicy(project).then(
      (p) => {
        setPolicy(p);
        setValue(p.ci_fail_on?.value ?? "none");
        setLocked(p.ci_fail_on?.locked ?? false);
      },
      () => setPolicy(null),
    );
  }, [project]);

  async function save() {
    setBusy(true);
    try {
      const next: SecurityPolicy = { ci_fail_on: { value, locked } };
      const { updated, failed } = await updateSecurityPolicy(project, next);
      setPolicy(next);
      toast.success(
        locked
          ? t("policy.appliedTo", { count: updated.length })
          : t("policy.saved"),
      );
      if (failed.length)
        toast.error(t("policy.notApplied", { list: failed.join(", ") }));
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!policy) return null;
  const dirty =
    value !== (policy.ci_fail_on?.value ?? "none") ||
    locked !== (policy.ci_fail_on?.locked ?? false);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("policy.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <label className="block space-y-1.5">
          <span className="font-medium">{t("policy.ciDefault")}</span>
          <select
            className={SELECT}
            value={value}
            disabled={busy}
            onChange={(e) => setValue(e.target.value as FailOn)}
          >
            {FAIL_ON_VALUES.map((v) => (
              <option key={v} value={v}>
                {t(`failOn.${v}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center justify-between gap-3">
          <span>
            <span className="font-medium">{t("policy.lock")}</span>
            <span className="block text-xs text-muted-foreground">
              {t("policy.lockHint")}
            </span>
          </span>
          <Switch checked={locked} onCheckedChange={setLocked} disabled={busy} />
        </label>
        {dirty && (
          <Button size="sm" onClick={save} disabled={busy}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t("common.save")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
