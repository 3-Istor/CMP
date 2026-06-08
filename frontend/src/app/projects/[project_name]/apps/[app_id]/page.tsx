"use client";

import { DeploymentHealthPanel } from "@/components/dashboard/DeploymentHealthPanel";
import { UserNav } from "@/components/layout/UserNav";
import { AppConfigPanel } from "@/components/projects/AppConfigPanel";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
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
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { deleteDeployment, getDeployment } from "@/lib/api";
import { useAppHealth, useDeploymentPolling } from "@/lib/hooks";
import type { Deployment } from "@/types";
import {
    Activity,
    ArrowLeft,
    CheckCircle2,
    ExternalLink,
    FileCode,
    Github,
    Loader2,
    RefreshCw,
    Shield,
    Trash2,
    XCircle,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_VARIANT: Record<
    Deployment["status"],
    "default" | "secondary" | "destructive" | "outline"
> = {
    running: "default",
    degraded: "secondary",
    pending: "outline",
    initializing: "outline",
    planning: "outline",
    deploying: "outline",
    failed: "destructive",
    deleting: "secondary",
    deleted: "secondary",
};

function StatusDot({ status }: { status: Deployment["status"] }) {
    if (status === "running")
        return <CheckCircle2 className="h-5 w-5 text-green-500" />;
    if (status === "failed")
        return <XCircle className="h-5 w-5 text-destructive" />;
    if (
        ["pending", "initializing", "planning", "deploying", "deleting"].includes(
            status,
        )
    )
        return <Loader2 className="h-5 w-5 animate-spin text-primary" />;
    return null;
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AppControlCenterPage() {
    const params = useParams();
    const router = useRouter();
    const projectName = params.project_name as string;
    const appId = Number(params.app_id);

    const [deployment, setDeployment] = useState<Deployment | null>(null);
    const [loading, setLoading] = useState(true);
    const [healthOpen, setHealthOpen] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [deleteConfirmName, setDeleteConfirmName] = useState("");
    const [deleting, setDeleting] = useState(false);

    // Poll only while in transient state
    const ACTIVE_STATUSES = new Set([
        "pending",
        "initializing",
        "planning",
        "deploying",
        "deleting",
    ]);
    const isActive = deployment ? ACTIVE_STATUSES.has(deployment.status) : false;
    const polled = useDeploymentPolling(isActive ? appId : null);

    const current = polled ?? deployment;

    const fetchDeployment = useCallback(async () => {
        try {
            const d = await getDeployment(appId);
            setDeployment(d);
        } catch {
            toast.error("Failed to load application");
        } finally {
            setLoading(false);
        }
    }, [appId]);

    useEffect(() => {
        fetchDeployment();
    }, [fetchDeployment]);

    // Health check for running/degraded apps
    const shouldFetchHealth =
        current?.status === "running" || current?.status === "degraded";
    const { health } = useAppHealth(
        shouldFetchHealth ? appId : null,
        10_000,
    );

    const handleDelete = async () => {
        if (!current || deleteConfirmName !== current.name) return;
        setDeleting(true);
        try {
            await deleteDeployment(appId);
            toast.success(`Deletion of "${current.name}" started`);
            setDeleteOpen(false);
            router.push(`/projects/${projectName}`);
        } catch (err) {
            toast.error(`Delete failed: ${err}`);
        } finally {
            setDeleting(false);
        }
    };

    // ── Loading skeleton ──────────────────────────────────────────────────────
    if (loading || !current) {
        return (
            <div className="min-h-screen">
                <header className="border-b px-6 py-4 flex items-center gap-3">
                    <span className="text-2xl">⚡</span>
                    <div>
                        <h1 className="text-lg font-bold leading-none">CMP</h1>
                        <p className="text-xs text-muted-foreground">Cloud Native Platform</p>
                    </div>
                    <div className="ml-auto">
                        <UserNav />
                    </div>
                </header>
                <main className="px-6 py-8 max-w-5xl mx-auto space-y-6">
                    <Skeleton className="h-8 w-48" />
                    <Skeleton className="h-24 w-full" />
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <div className="lg:col-span-2 space-y-4">
                            <Skeleton className="h-48" />
                            <Skeleton className="h-64" />
                        </div>
                        <Skeleton className="h-80" />
                    </div>
                </main>
            </div>
        );
    }

    const isKubernetes = current.provider_type === "kubernetes";
    const isRunning = current.status === "running";

    return (
        <div className="min-h-screen">
            {/* ── Header ── */}
            <header className="border-b px-6 py-4 flex items-center gap-3">
                <span className="text-2xl">⚡</span>
                <div>
                    <h1 className="text-lg font-bold leading-none">CMP</h1>
                    <p className="text-xs text-muted-foreground">Cloud Native Platform</p>
                </div>
                <div className="ml-auto">
                    <UserNav />
                </div>
            </header>

            <main className="px-6 py-8 max-w-5xl mx-auto space-y-6">

                {/* ── Breadcrumb ── */}
                <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => router.push(`/projects/${projectName}`)}
                    className="-ml-2"
                >
                    <ArrowLeft className="mr-2 h-4 w-4" />
                    {projectName}
                </Button>

                {/* ── App header ── */}
                <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div className="flex items-center gap-3">
                        {current.template_icon && (
                            <span className="text-4xl">{current.template_icon}</span>
                        )}
                        <div>
                            <div className="flex items-center gap-2">
                                <h2 className="text-2xl font-bold">{current.name}</h2>
                                <StatusDot status={current.status} />
                            </div>
                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                                <Badge
                                    variant={STATUS_VARIANT[current.status]}
                                    className="capitalize"
                                >
                                    {current.status}
                                </Badge>
                                <Badge variant="outline" className="capitalize text-xs">
                                    {current.provider_type === "kubernetes" ? "Kubernetes" : "Legacy"}
                                </Badge>
                                {current.k8s_namespace && (
                                    <Badge variant="outline" className="font-mono text-xs">
                                        ns: {current.k8s_namespace}
                                    </Badge>
                                )}
                            </div>
                            <p className="text-xs text-muted-foreground mt-1.5">
                                {current.template_name || current.template_id} ·{" "}
                                Deployed{" "}
                                {new Date(current.created_at).toLocaleDateString(undefined, {
                                    day: "numeric",
                                    month: "short",
                                    year: "numeric",
                                })}
                            </p>
                        </div>
                    </div>

                    {/* Header quick actions */}
                    <div className="flex items-center gap-2 flex-wrap">
                        {shouldFetchHealth && (
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setHealthOpen(true)}
                            >
                                <Activity className="mr-2 h-4 w-4" />
                                Health
                            </Button>
                        )}
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={fetchDeployment}
                        >
                            <RefreshCw className="mr-2 h-4 w-4" />
                            Refresh
                        </Button>
                        <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => {
                                setDeleteConfirmName("");
                                setDeleteOpen(true);
                            }}
                            disabled={["deleting", "deleted"].includes(current.status)}
                        >
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete App
                        </Button>
                    </div>
                </div>

                <Separator />

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">

                    {/* ── Left column: status + quick actions ── */}
                    <div className="lg:col-span-2 space-y-6">

                        {/* Deployment progress */}
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base">Deployment Status</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <DeploymentStepper deployment={current} />
                            </CardContent>
                        </Card>

                        {/* Quick Actions (Kubernetes only) */}
                        {isKubernetes && (
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base">Quick Actions</CardTitle>
                                </CardHeader>
                                <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                    {current.github_repo_url && (
                                        <Button variant="outline" size="sm" className="justify-start" asChild>
                                            <a
                                                href={current.github_repo_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                <Github className="mr-2 h-4 w-4" />
                                                Open GitHub Repository
                                                <ExternalLink className="ml-auto h-3 w-3" />
                                            </a>
                                        </Button>
                                    )}

                                    {current.argocd_app_name && (
                                        <Button variant="outline" size="sm" className="justify-start" asChild>
                                            <a
                                                href={`https://argocd.3istor.com/applications/${current.argocd_app_name}`}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                <FileCode className="mr-2 h-4 w-4" />
                                                View in ArgoCD
                                                <ExternalLink className="ml-auto h-3 w-3" />
                                            </a>
                                        </Button>
                                    )}

                                    {current.project_id && current.name && (
                                        <Button variant="outline" size="sm" className="justify-start" asChild>
                                            <a
                                                href={`https://vault.3istor.com/ui/vault/secrets/kvv2/show/projects/${current.project_id}/${current.name}`}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                <Shield className="mr-2 h-4 w-4" />
                                                Manage Secrets in Vault
                                                <ExternalLink className="ml-auto h-3 w-3" />
                                            </a>
                                        </Button>
                                    )}
                                </CardContent>
                            </Card>
                        )}

                        {/* Health summary (visible when health data is available) */}
                        {health && (
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base flex items-center gap-2">
                                        <Activity className="h-4 w-4 text-muted-foreground" />
                                        Health Summary
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-2">
                                            {health.status === "healthy" ? (
                                                <CheckCircle2 className="h-5 w-5 text-green-500" />
                                            ) : health.status === "degraded" ? (
                                                <Activity className="h-5 w-5 text-yellow-500" />
                                            ) : (
                                                <XCircle className="h-5 w-5 text-destructive" />
                                            )}
                                            <span className="text-sm font-medium capitalize">
                                                {health.status}
                                            </span>
                                        </div>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => setHealthOpen(true)}
                                        >
                                            View Details
                                        </Button>
                                    </div>
                                </CardContent>
                            </Card>
                        )}
                    </div>

                    {/* ── Right column: Day-2 config ── */}
                    <div className="space-y-6">
                        {isKubernetes && isRunning ? (
                            <AppConfigPanel deploymentId={appId} />
                        ) : isKubernetes && !isRunning ? (
                            <Card>
                                <CardContent className="pt-6">
                                    <p className="text-sm text-muted-foreground text-center">
                                        Configuration will be available once the application is{" "}
                                        <span className="font-medium text-foreground">running</span>.
                                    </p>
                                </CardContent>
                            </Card>
                        ) : (
                            /* Legacy deployments: show Terraform outputs */
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base">Terraform Outputs</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    {current.terraform_outputs ? (
                                        <div className="space-y-1.5 text-xs font-mono">
                                            {Object.entries(
                                                (() => {
                                                    try {
                                                        return JSON.parse(current.terraform_outputs) as Record<string, unknown>;
                                                    } catch {
                                                        return {};
                                                    }
                                                })(),
                                            ).map(([key, value]) => (
                                                <div key={key} className="flex gap-2 flex-wrap">
                                                    <span className="text-muted-foreground shrink-0">{key}:</span>
                                                    {typeof value === "string" &&
                                                        value.startsWith("http") ? (
                                                        <a
                                                            href={value}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            className="text-primary underline break-all"
                                                        >
                                                            {value}
                                                        </a>
                                                    ) : (
                                                        <span className="break-all">{String(value)}</span>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="text-sm text-muted-foreground">
                                            No outputs yet.
                                        </p>
                                    )}
                                </CardContent>
                            </Card>
                        )}
                    </div>
                </div>
            </main>

            {/* ── Health details dialog ── */}
            <DeploymentHealthPanel
                deploymentId={appId}
                deploymentName={current.name}
                open={healthOpen}
                onClose={() => setHealthOpen(false)}
            />

            {/* ── Delete confirmation dialog ── */}
            <Dialog
                open={deleteOpen}
                onOpenChange={(v) => !deleting && !v && setDeleteOpen(false)}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="text-destructive flex items-center gap-2">
                            <Trash2 className="h-5 w-5" />
                            Delete Application
                        </DialogTitle>
                        <DialogDescription>
                            This will destroy all cloud resources for{" "}
                            <span className="font-semibold">"{current.name}"</span> via
                            Terraform. This action cannot be undone.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-3 py-2">
                        <Label htmlFor="confirm-name" className="text-sm">
                            Type{" "}
                            <span className="font-mono font-semibold">{current.name}</span>{" "}
                            to confirm:
                        </Label>
                        <Input
                            id="confirm-name"
                            value={deleteConfirmName}
                            onChange={(e) => setDeleteConfirmName(e.target.value)}
                            placeholder={current.name}
                            disabled={deleting}
                        />
                    </div>

                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setDeleteOpen(false)}
                            disabled={deleting}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleDelete}
                            disabled={deleting || deleteConfirmName !== current.name}
                        >
                            {deleting ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Deleting…
                                </>
                            ) : (
                                <>
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Delete permanently
                                </>
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
