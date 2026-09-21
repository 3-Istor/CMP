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
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface CloudOption {
    value: TargetCloud;
    label: string;
    /** Why you'd pick this cloud, shown even while it's disabled — the point
     * is to let people plan ahead, not just to fill space. */
    advantages: string[];
    available: boolean;
}

const CLOUDS: CloudOption[] = [
    {
        value: "onprem",
        label: "On-premise",
        advantages: [
            "No data egress cost",
            "Full infrastructure control",
            "Already live — zero setup wait",
        ],
        available: true,
    },
    {
        value: "aws",
        label: "AWS",
        advantages: [
            "Widest catalog of managed services",
            "Mature IAM & compliance tooling",
            "Broad global region coverage",
        ],
        available: false,
    },
    {
        value: "gcp",
        label: "GCP",
        advantages: [
            "Native Kubernetes heritage",
            "Typically the most competitive compute pricing",
            "Strong data & AI tooling",
        ],
        available: false,
    },
];

interface Props {
    open: boolean;
    onClose: () => void;
    onCreated: (projectName: string) => void;
}

/** Validates a project name: lowercase, kebab-case, 2–45 chars */
function validateName(name: string): string | null {
    if (name.length < 2) return "Name must be at least 2 characters.";
    if (name.length > 45) return "Name must be at most 45 characters.";
    if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(name))
        return "Use lowercase letters, numbers, and hyphens only. Must start and end with a letter or number.";
    return null;
}

export function CreateProjectModal({ open, onClose, onCreated }: Props) {
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
            setError(validationError);
            return;
        }

        setLoading(true);
        setError(null);
        try {
            await createProject(trimmed, targetCloud);
            toast.success(
                `Project "${trimmed}" is being bootstrapped on ${targetCloud}. Keycloak groups and ArgoCD AppProject will be ready shortly.`,
                { duration: 6000 },
            );
            onCreated(trimmed);
            onClose();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            setError(msg);
            toast.error(`Failed to create project: ${msg}`);
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(v) => !loading && !v && onClose()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>Create a new Project</DialogTitle>
                    <DialogDescription>
                        A project groups your Kubernetes applications under a shared
                        Keycloak team, Vault policy, and ArgoCD AppProject. Bootstrap runs
                        in the background — it takes ~30 seconds.
                    </DialogDescription>
                </DialogHeader>

                <form onSubmit={handleSubmit} className="space-y-4 py-2">
                    <div className="space-y-2">
                        <Label htmlFor="project-name">
                            Project Name <span className="text-destructive">*</span>
                        </Label>
                        <Input
                            ref={inputRef}
                            id="project-name"
                            placeholder="my-team"
                            value={name}
                            maxLength={45}
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
                                    Lowercase, kebab-case. E.g.{" "}
                                    <span className="font-mono">platform-team</span>
                                </p>
                                <p className={`text-xs tabular-nums ${name.length > 40 ? "text-destructive" : "text-muted-foreground"}`}>
                                    {name.length}/45
                                </p>
                            </div>
                        )}
                    </div>

                    <div className="space-y-2">
                        <Label>
                            Target Cloud <span className="text-destructive">*</span>
                        </Label>
                        <div
                            role="radiogroup"
                            aria-label="Target Cloud"
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
                                                {cloud.label}
                                            </span>
                                            {selected ? (
                                                <Check className="h-3.5 w-3.5 text-primary" />
                                            ) : !cloud.available ? (
                                                <Badge
                                                    variant="secondary"
                                                    className="text-[10px] px-1.5 py-0"
                                                >
                                                    Coming soon
                                                </Badge>
                                            ) : null}
                                        </div>
                                        <ul className="space-y-0.5 text-muted-foreground">
                                            {cloud.advantages.map((a) => (
                                                <li key={a}>{a}</li>
                                            ))}
                                        </ul>
                                    </button>
                                );
                            })}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            AWS and GCP need a cluster registered by the infra team before
                            they can be selected.{" "}
                            <span className="text-foreground">
                                This cannot be changed later
                            </span>{" "}
                            — moving a project means re-creating it.
                        </p>
                    </div>

                    <div className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground space-y-1">
                        <div className="font-medium text-foreground mb-1.5">
                            What will be created
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                            Keycloak groups:{" "}
                            <span className="font-mono">
                                project-{name || "…"}-admins/members
                            </span>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary/70" />
                            Vault policy scoped to namespace
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary/40" />
                            ArgoCD AppProject on{" "}
                            <span className="font-mono">{targetCloud}</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-primary/25" />
                            Registry record:{" "}
                            <span className="font-mono">
                                registry/projects/{name || "…"}.yaml
                            </span>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={onClose}
                            disabled={loading}
                        >
                            Cancel
                        </Button>
                        <Button type="submit" disabled={loading || name.trim().length < 2}>
                            {loading ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Creating…
                                </>
                            ) : (
                                "Create Project"
                            )}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
