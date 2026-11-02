"use client";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createProject } from "@/lib/api";
import type { TargetCloud } from "@/types";
import { Check, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface CloudOption {
    value: TargetCloud;
    available: boolean;
}

const CLOUDS: CloudOption[] = [
    { value: "onprem", available: true },
    { value: "aws", available: true },
    { value: "gcp", available: true },
];

interface Props {
    open: boolean;
    onClose: () => void;
    onCreated: (projectName: string) => void;
}

type NameError = "tooShort" | "tooLong" | "invalid";

/** Validates a project name: lowercase, kebab-case, 2–40 chars (backend limit) */
function validateName(name: string): NameError | null {
    if (name.length < 2) return "tooShort";
    if (name.length > 40) return "tooLong";
    if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(name)) return "invalid";
    return null;
}

export function CreateProjectModal({ open, onClose, onCreated }: Props) {
    const t = useTranslations("projects.create");
    const tc = useTranslations("common");
    const [name, setName] = useState("");
    const [targetCloud, setTargetCloud] = useState<TargetCloud>("onprem");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    // Reset on open
    useEffect(() => {
        if (open) {
            setName("");
            setTargetCloud("onprem");
            setError(null);
            setTimeout(() => inputRef.current?.focus(), 100);
        }
    }, [open]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        const trimmed = name.trim();
        const validationError = validateName(trimmed);
        if (validationError) {
            setError(t(`errors.${validationError}`));
            return;
        }

        setLoading(true);
        setError(null);
        try {
            await createProject(trimmed, targetCloud);
            toast.success(
                t("toasts.created", {
                    name: trimmed,
                    cloud: t(`clouds.${targetCloud}.label`),
                }),
                { duration: 6000 },
            );
            onCreated(trimmed);
            onClose();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            setError(msg);
            toast.error(t("toasts.failed", { message: msg }));
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(v) => !loading && !v && onClose()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{t("title")}</DialogTitle>
                    <DialogDescription>{t("description")}</DialogDescription>
                </DialogHeader>

                <form onSubmit={handleSubmit} className="space-y-4 py-2">
                    <div className="space-y-2">
                        <Label htmlFor="project-name">
                            {t("name")} <span className="text-destructive">*</span>
                        </Label>
                        <Input
                            ref={inputRef}
                            id="project-name"
                            placeholder={t("namePlaceholder")}
                            value={name}
                            maxLength={40}
                            onChange={(e) => {
                                setName(e.target.value);
                                setError(null);
                            }}
                            disabled={loading}
                            autoComplete="off"
                        />
                        {error ? (
                            <p className="text-xs text-destructive">{error}</p>
                        ) : (
                            <div className="flex items-center justify-between">
                                <p className="text-xs text-muted-foreground">
                                    {t.rich("nameHint", {
                                        code: (chunks) => <span className="font-mono">{chunks}</span>,
                                    })}
                                </p>
                                <p className={`text-xs tabular-nums ${name.length > 35 ? "text-destructive" : "text-muted-foreground"}`}>
                                    {name.length}/40
                                </p>
                            </div>
                        )}
                    </div>

                    <div className="space-y-2">
                        <Label>
                            {t("cloud")} <span className="text-destructive">*</span>
                        </Label>
                        <div
                            role="radiogroup"
                            aria-label={t("cloud")}
                            className="grid grid-cols-1 gap-2 sm:grid-cols-3"
                        >
                            {CLOUDS.map((cloud) => {
                                const selected = targetCloud === cloud.value;
                                return (
                                    <button
                                        key={cloud.value}
                                        type="button"
                                        role="radio"
                                        aria-checked={selected}
                                        disabled={loading || !cloud.available}
                                        onClick={() => setTargetCloud(cloud.value)}
                                        className={`relative flex flex-col gap-1.5 rounded-lg border p-3 text-left text-xs transition-colors ${
                                            selected
                                                ? "border-primary bg-primary/5"
                                                : "border-input hover:bg-muted/40"
                                        } ${
                                            !cloud.available
                                                ? "cursor-not-allowed opacity-50 hover:bg-transparent"
                                                : "cursor-pointer"
                                        }`}
                                    >
                                        <div className="flex items-center justify-between gap-1">
                                            <span className="font-medium text-foreground">
                                                {t(`clouds.${cloud.value}.label`)}
                                            </span>
                                            {selected ? (
                                                <Check className="h-3.5 w-3.5 text-primary" />
                                            ) : !cloud.available ? (
                                                <Badge
                                                    variant="secondary"
                                                    className="text-[10px] px-1.5 py-0"
                                                >
                                                    {t("comingSoon")}
                                                </Badge>
                                            ) : null}
                                        </div>
                                        <ul className="space-y-0.5 text-muted-foreground">
                                            {(["advantage1", "advantage2", "advantage3"] as const).map((key) => (
                                                <li key={key}>{t(`clouds.${cloud.value}.${key}`)}</li>
                                            ))}
                                        </ul>
                                    </button>
                                );
                            })}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {t.rich("cloudNote", {
                                strong: (chunks) => <span className="text-foreground">{chunks}</span>,
                            })}
                        </p>
                    </div>

                    <div className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground space-y-1">
                        <div className="font-medium text-foreground mb-1.5">
                            {t("preview.title")}
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                            {t.rich("preview.keycloak", {
                                name: name || "…",
                                code: (chunks) => <span className="font-mono">{chunks}</span>,
                            })}
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary/70" />
                            {t("preview.vault")}
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary/40" />
                            {t.rich("preview.argocd", {
                                cloud: targetCloud,
                                code: (chunks) => <span className="font-mono">{chunks}</span>,
                            })}
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary/25" />
                            {t.rich("preview.registry", {
                                name: name || "…",
                                code: (chunks) => <span className="font-mono">{chunks}</span>,
                            })}
                        </div>
                    </div>

                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={onClose}
                            disabled={loading}
                        >
                            {tc("cancel")}
                        </Button>
                        <Button type="submit" disabled={loading || name.trim().length < 2}>
                            {loading ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    {t("submitting")}
                                </>
                            ) : (
                                t("submit")
                            )}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
