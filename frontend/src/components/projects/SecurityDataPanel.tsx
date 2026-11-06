"use client";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
    getSecurityData,
    previewSecurityData,
    updateSecurityData,
} from "@/lib/api";
import type { ExposurePreset, SecurityData } from "@/types";
import { Loader2, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

interface Props {
    deploymentId: number;
}

const RETENTION_PATTERN = /^[1-9][0-9]*[dwm]$/;

const EXPOSURE_OPTIONS: ExposurePreset[] = [
    "public",
    "project_users",
    "project_members",
    "project_admins",
];

const renderCode = (chunks: React.ReactNode) => (
    <span className="font-mono">{chunks}</span>
);

export function SecurityDataPanel({ deploymentId }: Props) {
    const t = useTranslations("apps.data");
    const tc = useTranslations("common");
    const [remote, setRemote] = useState<SecurityData | null>(null);
    const [exposure, setExposure] = useState<ExposurePreset | null>(null);
    const [backupEnabled, setBackupEnabled] = useState(false);
    const [keepAfterDelete, setKeepAfterDelete] = useState(false);
    const [retention, setRetention] = useState("");
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [preview, setPreview] = useState<{
        repo: string;
        previews: Record<string, { message: string; diff: string }>;
    } | null>(null);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await getSecurityData(deploymentId);
            setRemote(data);
            setExposure(
                data.exposure && data.exposure !== "custom"
                    ? data.exposure
                    : null,
            );
            setBackupEnabled(data.database?.backup.enabled ?? false);
            setKeepAfterDelete(data.database?.backup.keep_after_delete ?? false);
            setRetention(data.database?.backup.retention_policy ?? "");
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setLoading(false);
        }
    }, [deploymentId]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const retentionValid =
        retention === "" || RETENTION_PATTERN.test(retention);

    const buildUpdate = () => {
        if (!remote) return {};
        const update: import("@/types").SecurityDataUpdate = {};
        if (
            exposure &&
            remote.exposure !== null &&
            exposure !== remote.exposure
        ) {
            update.exposure = exposure;
        }
        const current = remote.database?.backup;
        if (current) {
            const backup: NonNullable<typeof update.backup> = {};
            if (backupEnabled !== current.enabled) backup.enabled = backupEnabled;
            if (keepAfterDelete !== current.keep_after_delete) {
                backup.keep_after_delete = keepAfterDelete;
            }
            if (retention !== "" && retention !== current.retention_policy) {
                backup.retention_policy = retention;
            }
            if (Object.keys(backup).length > 0) update.backup = backup;
        }
        return update;
    };

    const update = buildUpdate();
    const isDirty = Object.keys(update).length > 0;

    const handlePreview = async () => {
        setSaving(true);
        try {
            setPreview(await previewSecurityData(deploymentId, update));
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            toast.error(t("previewFailed", { message: msg }));
        } finally {
            setSaving(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await updateSecurityData(deploymentId, update);
            setPreview(null);
            toast.success(t("saved"), { duration: 7000 });
            await fetchData();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            toast.error(t("saveFailed", { message: msg }));
        } finally {
            setSaving(false);
        }
    };

    const canEdit = remote?.can_edit ?? false;
    const hasNothingToShow =
        remote !== null && remote.exposure === null && remote.database === null;

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                        <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                        <CardTitle className="text-base">
                            {t("title")}
                        </CardTitle>
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={fetchData}
                        disabled={loading || saving}
                        title={t("reload")}
                    >
                        <RefreshCw
                            className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                        />
                    </Button>
                </div>
                <CardDescription className="text-xs">
                    {t.rich("description", { code: renderCode })}
                </CardDescription>
            </CardHeader>

            <CardContent>
                {loading ? (
                    <div className="space-y-4">
                        {[1, 2, 3].map((i) => (
                            <Skeleton key={i} className="h-9 w-full" />
                        ))}
                    </div>
                ) : error ? (
                    <div className="rounded-lg border border-destructive bg-destructive/5 p-4 text-sm text-destructive space-y-2">
                        <p className="font-medium">{t("loadFailed")}</p>
                        <p className="text-xs">{error}</p>
                        <Button variant="outline" size="sm" onClick={fetchData}>
                            <RefreshCw className="mr-2 h-3.5 w-3.5" />
                            {tc("retry")}
                        </Button>
                    </div>
                ) : hasNothingToShow ? (
                    <p className="text-sm text-muted-foreground text-center py-4">
                        {t("empty")}
                    </p>
                ) : (
                    <div className="space-y-6">
                        {!canEdit && (
                            <Alert>
                                <AlertDescription>
                                    {t("adminsOnly")}
                                </AlertDescription>
                            </Alert>
                        )}

                        {remote?.exposure !== null && (
                            <fieldset className="space-y-3" disabled={!canEdit || saving}>
                                <legend className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                                    {t("exposure")}
                                </legend>
                                {remote?.exposure === "custom" && (
                                    <p className="text-xs text-muted-foreground">
                                        {t("customExposure")}
                                    </p>
                                )}
                                {EXPOSURE_OPTIONS.map((option) => (
                                    <label
                                        key={option}
                                        className="flex items-start gap-3 cursor-pointer text-sm"
                                    >
                                        <input
                                            type="radio"
                                            name={`exposure-${deploymentId}`}
                                            value={option}
                                            checked={exposure === option}
                                            onChange={() => setExposure(option)}
                                            className="mt-1"
                                        />
                                        <span>
                                            <span className="font-medium">
                                                {t(`exposureOptions.${option}.label`)}
                                            </span>
                                            <span className="block text-xs text-muted-foreground">
                                                {t(`exposureOptions.${option}.hint`)}
                                            </span>
                                        </span>
                                    </label>
                                ))}
                            </fieldset>
                        )}

                        {remote?.database && (
                            <fieldset className="space-y-4" disabled={!canEdit || saving}>
                                <legend className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                                    {t("database")}
                                </legend>

                                <div className="flex items-center justify-between gap-4">
                                    <Label htmlFor="backup-enabled" className="text-sm">
                                        {t("backupEnabled")}
                                    </Label>
                                    <Switch
                                        id="backup-enabled"
                                        checked={backupEnabled}
                                        onCheckedChange={setBackupEnabled}
                                    />
                                </div>

                                <div className="space-y-2">
                                    <div className="flex items-center justify-between gap-4">
                                        <Label
                                            htmlFor="backup-keep"
                                            className="text-sm"
                                        >
                                            {t("backupKeep")}
                                        </Label>
                                        <Switch
                                            id="backup-keep"
                                            checked={keepAfterDelete}
                                            onCheckedChange={setKeepAfterDelete}
                                        />
                                    </div>
                                    {!keepAfterDelete && (
                                        <p className="text-xs text-destructive">
                                            {t("backupKeepOff")}
                                        </p>
                                    )}
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="backup-retention" className="text-sm">
                                        {t("retention")}
                                    </Label>
                                    <Input
                                        id="backup-retention"
                                        value={retention}
                                        placeholder="7d"
                                        onChange={(e) => setRetention(e.target.value.trim())}
                                        aria-invalid={!retentionValid}
                                    />
                                    <p
                                        className={`text-xs ${retentionValid ? "text-muted-foreground" : "text-destructive"}`}
                                    >
                                        {t("retentionHint")}
                                    </p>
                                </div>
                            </fieldset>
                        )}

                        {canEdit && isDirty && (
                            <div className="pt-2 border-t border-border">
                                <Button
                                    onClick={handlePreview}
                                    disabled={saving || !retentionValid}
                                    className="w-full"
                                >
                                    {saving ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            {t("saving")}
                                        </>
                                    ) : (
                                        <>
                                            <Save className="mr-2 h-4 w-4" />
                                            {t("save")}
                                        </>
                                    )}
                                </Button>
                            </div>
                        )}
                    </div>
                )}
            </CardContent>

            {preview && (
                <Dialog open onOpenChange={(open) => !open && setPreview(null)}>
                    <DialogContent className="sm:max-w-2xl">
                        <DialogHeader>
                            <DialogTitle>{t("confirmTitle")}</DialogTitle>
                            <DialogDescription>
                                {t("confirmDescription", { repo: preview.repo })}
                            </DialogDescription>
                        </DialogHeader>
                        <div className="max-h-[60vh] space-y-4 overflow-y-auto">
                            {Object.entries(preview.previews).map(([file, p]) => (
                                <div key={file} className="space-y-1.5">
                                    <p className="font-mono text-xs">{p.message}</p>
                                    <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-[11px] leading-relaxed">
                                        {p.diff.split("\n").map((line, i) => (
                                            <span
                                                key={i}
                                                className={
                                                    line.startsWith("+") && !line.startsWith("+++")
                                                        ? "block text-green-700 dark:text-green-400"
                                                        : line.startsWith("-") && !line.startsWith("---")
                                                          ? "block text-red-700 dark:text-red-400"
                                                          : "block text-muted-foreground"
                                                }
                                            >
                                                {line || " "}
                                            </span>
                                        ))}
                                    </pre>
                                </div>
                            ))}
                        </div>
                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => setPreview(null)}
                                disabled={saving}
                            >
                                {tc("cancel")}
                            </Button>
                            <Button onClick={handleSave} disabled={saving}>
                                {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                {t("confirm")}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            )}
        </Card>
    );
}
