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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createProject } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface Props {
    open: boolean;
    onClose: () => void;
    onCreated: () => void;
}

/** Validates a project name: lowercase, kebab-case, 2–40 chars */
function validateName(name: string): string | null {
    if (name.length < 2) return "Name must be at least 2 characters.";
    if (name.length > 40) return "Name must be at most 40 characters.";
    if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(name))
        return "Use lowercase letters, numbers, and hyphens only. Must start and end with a letter or number.";
    return null;
}

export function CreateProjectModal({ open, onClose, onCreated }: Props) {
    const [name, setName] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    // Reset on open
    useEffect(() => {
        if (open) {
            setName("");
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
            await createProject(trimmed);
            toast.success(
                `Project "${trimmed}" is being bootstrapped. Keycloak groups and ArgoCD AppProject will be ready shortly.`,
                { duration: 6000 },
            );
            onCreated();
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
                            <p className="text-xs text-muted-foreground">
                                Lowercase, kebab-case. E.g.{" "}
                                <span className="font-mono">platform-team</span>
                            </p>
                        )}
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
                            ArgoCD AppProject
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
