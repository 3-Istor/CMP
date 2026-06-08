"use client";

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
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { getDeploymentConfig, updateDeploymentConfig } from "@/lib/api";
import type { DeploymentConfig } from "@/types";
import { Loader2, RefreshCw, Save, Settings2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface Props {
    deploymentId: number;
}

// ── Value helpers ─────────────────────────────────────────────────────────────

type ConfigValue = string | number | boolean | Record<string, unknown>;

/**
 * Determine the best input widget for a config value.
 * We infer from the key name and the value type.
 */
function guessFieldType(
    key: string,
    value: ConfigValue,
): "switch" | "slider" | "text" | "number" | "object" {
    if (typeof value === "boolean") return "switch";
    if (typeof value === "object" && value !== null) return "object";
    const keyLower = key.toLowerCase();
    if (keyLower.includes("replica") || keyLower.includes("count")) {
        return "slider";
    }
    if (typeof value === "number") return "number";
    return "text";
}

function humanLabel(key: string): string {
    return key
        .replace(/([A-Z])/g, " $1")
        .replace(/_/g, " ")
        .replace(/^./, (s) => s.toUpperCase())
        .trim();
}

// ── Recursive config form ─────────────────────────────────────────────────────

interface FieldProps {
    keyPath: string;
    value: ConfigValue;
    onChange: (keyPath: string, newVal: ConfigValue) => void;
    depth?: number;
}

function ConfigField({ keyPath, value, onChange, depth = 0 }: FieldProps) {
    const leafKey = keyPath.split(".").pop()!;
    const fieldType = guessFieldType(leafKey, value);
    const label = humanLabel(leafKey);

    if (fieldType === "object") {
        const obj = value as Record<string, unknown>;
        return (
            <div className={`space-y-3 ${depth > 0 ? "pl-4 border-l border-border" : ""}`}>
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {label}
                </p>
                {Object.entries(obj).map(([k, v]) => (
                    <ConfigField
                        key={`${keyPath}.${k}`}
                        keyPath={`${keyPath}.${k}`}
                        value={v as ConfigValue}
                        onChange={onChange}
                        depth={depth + 1}
                    />
                ))}
            </div>
        );
    }

    if (fieldType === "switch") {
        return (
            <div className="flex items-center justify-between gap-4">
                <Label htmlFor={keyPath} className="text-sm">
                    {label}
                </Label>
                <Switch
                    id={keyPath}
                    checked={value as boolean}
                    onCheckedChange={(checked) => onChange(keyPath, checked)}
                />
            </div>
        );
    }

    if (fieldType === "slider") {
        const num = Number(value);
        return (
            <div className="space-y-2">
                <div className="flex items-center justify-between">
                    <Label htmlFor={keyPath} className="text-sm">
                        {label}
                    </Label>
                    <span className="text-sm font-mono tabular-nums">{num}</span>
                </div>
                <Slider
                    id={keyPath}
                    min={1}
                    max={10}
                    step={1}
                    value={[num]}
                    onValueChange={([v]) => onChange(keyPath, v)}
                    className="w-full"
                />
            </div>
        );
    }

    if (fieldType === "number") {
        return (
            <div className="space-y-1.5">
                <Label htmlFor={keyPath} className="text-sm">
                    {label}
                </Label>
                <Input
                    id={keyPath}
                    type="number"
                    value={String(value)}
                    onChange={(e) => onChange(keyPath, Number(e.target.value))}
                />
            </div>
        );
    }

    // text
    return (
        <div className="space-y-1.5">
            <Label htmlFor={keyPath} className="text-sm">
                {label}
            </Label>
            <Input
                id={keyPath}
                type="text"
                value={String(value)}
                onChange={(e) => onChange(keyPath, e.target.value)}
            />
        </div>
    );
}

// ── Deep set helper (immutable) ───────────────────────────────────────────────

function deepSet(
    obj: Record<string, unknown>,
    keyPath: string,
    value: unknown,
): Record<string, unknown> {
    const keys = keyPath.split(".");
    if (keys.length === 1) {
        return { ...obj, [keyPath]: value };
    }
    const [head, ...rest] = keys;
    return {
        ...obj,
        [head]: deepSet(
            (obj[head] as Record<string, unknown>) ?? {},
            rest.join("."),
            value,
        ),
    };
}

// ── Main component ────────────────────────────────────────────────────────────

export function AppConfigPanel({ deploymentId }: Props) {
    const [remoteConfig, setRemoteConfig] = useState<DeploymentConfig | null>(
        null,
    );
    const [localConfig, setLocalConfig] = useState<Record<string, unknown>>({});
    const [isDirty, setIsDirty] = useState(false);
    const [loadingConfig, setLoadingConfig] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // Keep the SHA in a ref so it is always fresh when we save
    const shaRef = useRef<string>("");

    const fetchConfig = useCallback(async () => {
        setLoadingConfig(true);
        setError(null);
        try {
            const data = await getDeploymentConfig(deploymentId);
            setRemoteConfig(data);
            setLocalConfig(data.config as Record<string, unknown>);
            shaRef.current = data._sha;
            setIsDirty(false);
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            setError(msg);
        } finally {
            setLoadingConfig(false);
        }
    }, [deploymentId]);

    useEffect(() => {
        fetchConfig();
    }, [fetchConfig]);

    const handleChange = useCallback((keyPath: string, newVal: ConfigValue) => {
        setLocalConfig((prev) => deepSet(prev, keyPath, newVal));
        setIsDirty(true);
    }, []);

    const handleSave = async () => {
        setSaving(true);
        try {
            await updateDeploymentConfig(deploymentId, {
                ...localConfig,
                _sha: shaRef.current,
            });
            toast.success(
                "Configuration saved. ArgoCD will synchronise the new values in a few moments.",
                { duration: 7000 },
            );
            // Reload to get the fresh SHA
            await fetchConfig();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            if (msg.includes("409") || msg.toLowerCase().includes("conflict")) {
                toast.error(
                    "Conflict: the file was modified since you last loaded it. Reloading…",
                );
                await fetchConfig();
            } else {
                toast.error(`Failed to save: ${msg}`);
            }
        } finally {
            setSaving(false);
        }
    };

    // ── Render ──────────────────────────────────────────────────────────────────

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                        <Settings2 className="h-4 w-4 text-muted-foreground" />
                        <CardTitle className="text-base">Infrastructure Configuration</CardTitle>
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={fetchConfig}
                        disabled={loadingConfig || saving}
                        title="Reload config from GitHub"
                    >
                        <RefreshCw
                            className={`h-4 w-4 ${loadingConfig ? "animate-spin" : ""}`}
                        />
                    </Button>
                </div>
                <CardDescription className="text-xs">
                    Changes are committed to{" "}
                    <span className="font-mono">deploy/values.yaml</span> on{" "}
                    <span className="font-mono">main</span>. ArgoCD syncs automatically.
                    {remoteConfig?.repo && (
                        <>
                            {" "}
                            Repo:{" "}
                            <span className="font-mono text-foreground">
                                {remoteConfig.repo}
                            </span>
                        </>
                    )}
                </CardDescription>
            </CardHeader>

            <CardContent>
                {loadingConfig ? (
                    <div className="space-y-4">
                        {[1, 2, 3, 4].map((i) => (
                            <Skeleton key={i} className="h-9 w-full" />
                        ))}
                    </div>
                ) : error ? (
                    <div className="rounded-lg border border-destructive bg-destructive/5 p-4 text-sm text-destructive space-y-2">
                        <p className="font-medium">Failed to load configuration</p>
                        <p className="text-xs opacity-80">{error}</p>
                        <Button variant="outline" size="sm" onClick={fetchConfig}>
                            <RefreshCw className="mr-2 h-3.5 w-3.5" />
                            Retry
                        </Button>
                    </div>
                ) : (
                    <div className="space-y-5">
                        {Object.entries(localConfig).map(([key, value]) => (
                            <ConfigField
                                key={key}
                                keyPath={key}
                                value={value as ConfigValue}
                                onChange={handleChange}
                            />
                        ))}

                        {Object.keys(localConfig).length === 0 && (
                            <p className="text-sm text-muted-foreground py-4 text-center">
                                No configuration keys found in{" "}
                                <span className="font-mono">deploy/values.yaml</span>
                            </p>
                        )}

                        {isDirty && (
                            <div className="pt-2 border-t border-border">
                                <Button
                                    onClick={handleSave}
                                    disabled={saving}
                                    className="w-full"
                                >
                                    {saving ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Saving…
                                        </>
                                    ) : (
                                        <>
                                            <Save className="mr-2 h-4 w-4" />
                                            Save Changes
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
