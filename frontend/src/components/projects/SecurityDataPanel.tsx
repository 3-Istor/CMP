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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { getSecurityData, updateSecurityData } from "@/lib/api";
import type { ExposurePreset, SecurityData } from "@/types";
import { Loader2, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

interface Props {
    deploymentId: number;
}

const RETENTION_PATTERN = /^[1-9][0-9]*[dwm]$/;

const EXPOSURE_OPTIONS: {
    value: ExposurePreset;
    label: string;
    hint: string;
}[] = [
    {
        value: "public",
        label: "Public",
        hint: "Accessible sans connexion.",
    },
    {
        value: "project_users",
        label: "Utilisateurs connectés du projet",
        hint: "Toute personne connectée au royaume du projet.",
    },
    {
        value: "project_members",
        label: "Membres du projet",
        hint: "Membres et admins du projet.",
    },
    {
        value: "project_admins",
        label: "Admins du projet",
        hint: "Admins du projet uniquement.",
    },
];

export function SecurityDataPanel({ deploymentId }: Props) {
    const [remote, setRemote] = useState<SecurityData | null>(null);
    const [exposure, setExposure] = useState<ExposurePreset | null>(null);
    const [backupEnabled, setBackupEnabled] = useState(false);
    const [keepAfterDelete, setKeepAfterDelete] = useState(false);
    const [retention, setRetention] = useState("");
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
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

    const handleSave = async () => {
        setSaving(true);
        try {
            await updateSecurityData(deploymentId, update);
            toast.success(
                "Paramètres enregistrés. ArgoCD les appliquera dans quelques instants.",
                { duration: 7000 },
            );
            await fetchData();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            toast.error(`Échec de l'enregistrement : ${msg}`);
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
                            Exposition et données
                        </CardTitle>
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={fetchData}
                        disabled={loading || saving}
                        title="Recharger depuis GitHub"
                    >
                        <RefreshCw
                            className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                        />
                    </Button>
                </div>
                <CardDescription className="text-xs">
                    Ces réglages sont écrits dans les fichiers{" "}
                    <span className="font-mono">deploy/values*.yaml</span> du
                    dépôt de l&apos;application. Ils peuvent aussi être modifiés
                    dans le code.
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
                        <p className="font-medium">Chargement impossible</p>
                        <p className="text-xs opacity-80">{error}</p>
                        <Button variant="outline" size="sm" onClick={fetchData}>
                            <RefreshCw className="mr-2 h-3.5 w-3.5" />
                            Réessayer
                        </Button>
                    </div>
                ) : hasNothingToShow ? (
                    <p className="text-sm text-muted-foreground text-center py-4">
                        Aucun réglage d&apos;exposition ou de base de données
                        pour cette application.
                    </p>
                ) : (
                    <div className="space-y-6">
                        {!canEdit && (
                            <Alert>
                                <AlertDescription>
                                    Seuls les admins du projet peuvent modifier
                                    ces réglages.
                                </AlertDescription>
                            </Alert>
                        )}

                        {remote?.exposure !== null && (
                            <fieldset className="space-y-3" disabled={!canEdit || saving}>
                                <legend className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                                    Exposition
                                </legend>
                                {remote?.exposure === "custom" && (
                                    <p className="text-xs text-muted-foreground">
                                        Personnalisé (défini dans le code).
                                        Choisir un profil ci-dessous le
                                        remplacera.
                                    </p>
                                )}
                                {EXPOSURE_OPTIONS.map((option) => (
                                    <label
                                        key={option.value}
                                        className="flex items-start gap-3 cursor-pointer text-sm"
                                    >
                                        <input
                                            type="radio"
                                            name={`exposure-${deploymentId}`}
                                            value={option.value}
                                            checked={exposure === option.value}
                                            onChange={() => setExposure(option.value)}
                                            className="mt-1"
                                        />
                                        <span>
                                            <span className="font-medium">
                                                {option.label}
                                            </span>
                                            <span className="block text-xs text-muted-foreground">
                                                {option.hint}
                                            </span>
                                        </span>
                                    </label>
                                ))}
                            </fieldset>
                        )}

                        {remote?.database && (
                            <fieldset className="space-y-4" disabled={!canEdit || saving}>
                                <legend className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                                    Sauvegardes de la base de données
                                </legend>

                                <div className="flex items-center justify-between gap-4">
                                    <Label htmlFor="backup-enabled" className="text-sm">
                                        Activer les sauvegardes
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
                                            Conserver les sauvegardes après
                                            suppression
                                        </Label>
                                        <Switch
                                            id="backup-keep"
                                            checked={keepAfterDelete}
                                            onCheckedChange={setKeepAfterDelete}
                                        />
                                    </div>
                                    {!keepAfterDelete && (
                                        <p className="text-xs text-destructive">
                                            Désactivé : supprimer
                                            l&apos;application ou le projet
                                            supprime aussi toutes ses
                                            sauvegardes, définitivement.
                                        </p>
                                    )}
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="backup-retention" className="text-sm">
                                        Rétention
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
                                        Nombre suivi de d (jours), w (semaines)
                                        ou m (mois), par exemple 7d, 4w ou 3m.
                                    </p>
                                </div>
                            </fieldset>
                        )}

                        {canEdit && isDirty && (
                            <div className="pt-2 border-t border-border">
                                <Button
                                    onClick={handleSave}
                                    disabled={saving || !retentionValid}
                                    className="w-full"
                                >
                                    {saving ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Enregistrement…
                                        </>
                                    ) : (
                                        <>
                                            <Save className="mr-2 h-4 w-4" />
                                            Enregistrer
                                        </>
                                    )}
                                </Button>
                            </div>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
