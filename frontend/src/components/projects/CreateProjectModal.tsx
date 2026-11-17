"use client";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DOCS_URL } from "@/components/layout/shellData";
import {
    ApiError,
    createProject,
    getGlobalHealth,
    getMyImportableRepos,
    getProjects,
} from "@/lib/api";
import { clearProjectsCache } from "@/lib/hooks";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import type { ClusterStatus, ImportableRepo, TargetCloud } from "@/types";
import { isProjectFailed } from "@/types";
import { Check, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type ClusterState = ClusterStatus["state"] | "checking";

type Choice = "onprem" | "public";
type Provider = "auto" | "aws" | "gcp";

const PUBLIC_CLOUDS: Exclude<TargetCloud, "onprem">[] = ["aws", "gcp"];
// Best non-up state to show on a disabled card.
const STATE_ORDER: ClusterState[] = ["checking", "starting", "down", "unknown"];
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
    const tNav = useTranslations("projectNav");
    const [name, setName] = useState("");
    const [choice, setChoice] = useState<Choice>("onprem");
    const [provider, setProvider] = useState<Provider>("auto");
    const isAdmin = usePlatformAdmin();
    // A public cloud is offered only while its cluster is up: the backend
    // refuses the others, since nothing would reconcile the project there.
    const [clusterStates, setClusterStates] = useState<
        Partial<Record<TargetCloud, ClusterState>>
    >({});
    const stateOf = (cloud: Exclude<TargetCloud, "onprem">): ClusterState =>
        clusterStates[cloud] ?? "checking";
    const publicUp = PUBLIC_CLOUDS.some((c) => stateOf(c) === "up");
    const publicState = STATE_ORDER.find((st) =>
        PUBLIC_CLOUDS.some((c) => stateOf(c) === st),
    ) ?? "unknown";
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
            setChoice("onprem");
            setProvider("auto");
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
                setSource("empty");
                if (err instanceof ApiError && err.status === 404) {
                    setRepos("off");
                } else {
                    // Left unloaded: the next click tries again.
                    toast.error(t("repoFailed"));
                }
            });
    };

    // Parents pass an inline onClose: a ref keeps their re-renders from restarting the poll.
    const onCloseRef = useRef(onClose);
    useEffect(() => {
        onCloseRef.current = onClose;
    });

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
                    onCloseRef.current();
                    router.push(
                        `/projects/${encodeURIComponent(waiting)}/import?repo=${encodeURIComponent(repo)}`,
                    );
                } else if (isProjectFailed(project)) {
                    clearInterval(timer);
                    setWaiting(null);
                    setError(project.step_message || t("failed"));
                }
            } catch {
                // A missed poll is retried on the next tick.
            }
        }, WAIT_POLL_MS);
        return () => clearInterval(timer);
    }, [open, waiting, repo, router, t]);

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
            const target =
                choice === "onprem"
                    ? "onprem"
                    : isAdmin && provider !== "auto"
                      ? provider
                      : "public";
            const created = await createProject(trimmed, target);
            toast.success(
                t("toasts.created", {
                    name: trimmed,
                    cloud: tNav(`cloud.${created.target_cloud}`),
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
                        <div className="flex items-center justify-between">
                            <Label>{t("whereItRuns")}</Label>
                            <a
                                href={`${DOCS_URL}06-multicloud/08-placement-cloud-public/#comparaison-affichee`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                            >
                                {t("compare")}
                            </a>
                        </div>
                        <div
                            role="radiogroup"
                            aria-label={t("whereItRuns")}
                            className="grid grid-cols-2 gap-2"
                        >
                            {(["onprem", "public"] as const).map((value) => {
                                const available = value === "onprem" || publicUp;
                                const selected = choice === value;
                                return (
                                    <button
                                        key={value}
                                        type="button"
                                        role="radio"
                                        aria-checked={selected}
                                        disabled={loading || !available}
                                        onClick={() => setChoice(value)}
                                        className={`relative flex flex-col gap-1 rounded-lg border p-3 text-left text-xs transition-colors ${
                                            selected
                                                ? "border-primary bg-primary/5"
                                                : "border-input hover:bg-muted/40"
                                        } ${
                                            !available
                                                ? "cursor-not-allowed opacity-50 hover:bg-transparent"
                                                : "cursor-pointer"
                                        }`}
                                    >
                                        <div className="flex items-center justify-between gap-1">
                                            <span className="font-medium text-foreground">
                                                {t(`clouds.${value}.label`)}
                                            </span>
                                            {selected ? (
                                                <Check className="h-3.5 w-3.5 text-primary" />
                                            ) : !available ? (
                                                <Badge
                                                    variant="secondary"
                                                    className="text-[10px] px-1.5 py-0"
                                                >
                                                    {t(`clusterState.${publicState}`)}
                                                </Badge>
                                            ) : null}
                                        </div>
                                        <span className="text-muted-foreground">
                                            {t(`clouds.${value}.hint`)}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                        {isAdmin && choice === "public" && (
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-muted-foreground">
                                    {t("provider")}
                                </span>
                                <div
                                    role="radiogroup"
                                    aria-label={t("provider")}
                                    className="flex gap-1"
                                >
                                    {(["auto", ...PUBLIC_CLOUDS] as const).map((value) => (
                                        <button
                                            key={value}
                                            type="button"
                                            role="radio"
                                            aria-checked={provider === value}
                                            disabled={loading || (value !== "auto" && stateOf(value) !== "up")}
                                            onClick={() => setProvider(value)}
                                            className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                                                provider === value
                                                    ? "border-primary bg-primary/5 text-foreground"
                                                    : "border-input text-muted-foreground hover:bg-muted/40"
                                            }`}
                                        >
                                            {value === "auto" ? t("auto") : t(`clouds.${value}.label`)}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
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
