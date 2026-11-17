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
import {
    ApiError,
    createProject,
    getGlobalHealth,
    getMyImportableRepos,
    getProjects,
} from "@/lib/api";
import { clearProjectsCache } from "@/lib/hooks";
import type { ClusterStatus, ImportableRepo, TargetCloud } from "@/types";
import { isProjectFailed } from "@/types";
import { Check, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type ClusterState = ClusterStatus["state"] | "checking";

const CLOUDS: TargetCloud[] = ["onprem", "aws", "gcp"];
// Same cadence as useProjectsLive while a bootstrap runs.
const WAIT_POLL_MS = 3000;

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
    // A public cloud is offered only while its cluster is up: the backend
    // refuses the others, since nothing would reconcile the project there.
    const [clusterStates, setClusterStates] = useState<
        Partial<Record<TargetCloud, ClusterState>>
    >({});
    const stateOf = (cloud: TargetCloud): ClusterState | "up" =>
        cloud === "onprem" ? "up" : (clusterStates[cloud] ?? "checking");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const router = useRouter();
    const [source, setSource] = useState<"empty" | "repo">("empty");
    // null: not loaded yet; "off": import is disabled on this platform.
    const [repos, setRepos] = useState<ImportableRepo[] | "off" | null>(null);
    const [repo, setRepo] = useState("");
    // The project being created, while the dialog waits to open its import.
    const [waiting, setWaiting] = useState<string | null>(null);
    const [step, setStep] = useState<string | null>(null);

    // Reset on open
    useEffect(() => {
        if (open) {
            setName("");
            setTargetCloud("onprem");
            setError(null);
            setSource("empty");
            setWaiting(null);
            setStep(null);
            setClusterStates({ aws: "checking", gcp: "checking" });
            getGlobalHealth()
                .then((health) =>
                    setClusterStates(
                        Object.fromEntries(
                            health.clusters.map((c) => [c.cloud, c.state]),
                        ),
                    ),
                )
                .catch(() => setClusterStates({ aws: "unknown", gcp: "unknown" }));
            setTimeout(() => inputRef.current?.focus(), 100);
        }
    }, [open]);

    const chooseRepo = () => {
        setSource("repo");
        if (repos !== null) return;
        getMyImportableRepos()
            .then((list) => {
                setRepos(list);
                setRepo(list[0]?.full_name ?? "");
            })
            .catch((err) => {
                const off = err instanceof ApiError && err.status === 404;
                setRepos(off ? "off" : []);
                if (off) setSource("empty");
            });
    };

    // Poll until the bootstrap ends, then open the import of the chosen repository.
    useEffect(() => {
        if (!open || !waiting) return;
        const timer = setInterval(async () => {
            try {
                const project = (await getProjects()).find((p) => p.name === waiting);
                if (!project) return;
                setStep(project.step_message);
                if (project.status === "active") {
                    clearInterval(timer);
                    clearProjectsCache();
                    onClose();
                    router.push(
                        `/projects/${encodeURIComponent(waiting)}/import?repo=${encodeURIComponent(repo)}`,
                    );
                } else if (isProjectFailed(project)) {
                    clearInterval(timer);
                    setWaiting(null);
                    setError(project.step_message || t("toasts.failed", { message: "" }));
                }
            } catch {
                // A missed poll is retried on the next tick.
            }
        }, WAIT_POLL_MS);
        return () => clearInterval(timer);
    }, [open, waiting, repo, onClose, router, t]);

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
            if (source === "repo") {
                setWaiting(trimmed);
            } else {
                onClose();
            }
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

                {waiting ? (
                    <div role="status" className="space-y-2 py-6 text-center">
                        <Loader2 className="mx-auto h-6 w-6 animate-spin text-primary" aria-hidden />
                        <p className="text-sm font-medium">{t("waiting")}</p>
                        {step && <p className="text-xs text-muted-foreground">{step}</p>}
                    </div>
                ) : (
                <form onSubmit={handleSubmit} className="space-y-4 py-2">
                    <div className="space-y-2">
                        <Label>{t("source")}</Label>
                        <div role="radiogroup" aria-label={t("source")} className="grid grid-cols-2 gap-2">
                            {(["empty", "repo"] as const).map((value) => {
                                const off = value === "repo" && repos === "off";
                                return (
                                    <button
                                        key={value}
                                        type="button"
                                        role="radio"
                                        aria-checked={source === value}
                                        disabled={loading || off}
                                        onClick={() => (value === "repo" ? chooseRepo() : setSource("empty"))}
                                        className={`flex items-center justify-between gap-2 rounded-lg border p-3 text-left text-sm font-medium transition-colors ${
                                            source === value ? "border-primary bg-primary/5" : "border-input hover:bg-muted/40"
                                        } ${off ? "cursor-not-allowed opacity-50 hover:bg-transparent" : "cursor-pointer"}`}
                                    >
                                        {t(value === "empty" ? "sourceEmpty" : "sourceRepo")}
                                        {source === value ? (
                                            <Check className="h-3.5 w-3.5 text-primary" aria-hidden />
                                        ) : off ? (
                                            <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                                                {t("repoOff")}
                                            </Badge>
                                        ) : null}
                                    </button>
                                );
                            })}
                        </div>
                        {source === "repo" &&
                            (repos === null ? (
                                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                                    {t("repo")}
                                </p>
                            ) : repos !== "off" && repos.length > 0 ? (
                                <div className="space-y-1">
                                    <Label htmlFor="project-repo">{t("repo")}</Label>
                                    <select
                                        id="project-repo"
                                        value={repo}
                                        onChange={(e) => setRepo(e.target.value)}
                                        disabled={loading}
                                        className="h-9 w-full rounded-lg border border-input bg-transparent px-2.5 font-mono text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground"
                                    >
                                        {repos.map((r) => (
                                            <option key={r.full_name} value={r.full_name}>
                                                {r.full_name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                            ) : (
                                <p className="text-xs text-muted-foreground">
                                    {t("repoNone")} ·{" "}
                                    <Link href="/account" className="text-primary hover:underline" onClick={onClose}>
                                        {t("repoLink")}
                                    </Link>
                                </p>
                            ))}
                    </div>

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
                            {CLOUDS.map((value) => {
                                const state = stateOf(value);
                                const cloud = { value, available: state === "up" };
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
                                                    {t(`clusterState.${state}`)}
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
                        <Button
                            type="submit"
                            disabled={loading || name.trim().length < 2 || (source === "repo" && !repo)}
                        >
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
                )}
            </DialogContent>
        </Dialog>
    );
}
