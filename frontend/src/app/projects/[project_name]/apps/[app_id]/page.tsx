"use client";

import { Github } from "@/components/icons/Github";
import { UserNav } from "@/components/layout/UserNav";
import { AppConfigPanel } from "@/components/projects/AppConfigPanel";
import { ProjectFinopsPanel } from "@/components/finops/ProjectFinopsPanel";
import { SecurityDataPanel } from "@/components/projects/SecurityDataPanel";
import { SecurityAlert } from "@/components/security/SecurityAlert";
import { SecurityDashboard } from "@/components/security/SecurityDashboard";
import { ActivityTab } from "@/components/activity/ActivityTab";
import { AlertsPanel } from "@/components/alerting/AlertsPanel";
import { DeploymentHealth } from "@/components/projects/DeploymentHealth";
import { DeploymentLogs } from "@/components/projects/DeploymentLogs";
import { DeploymentStepper } from "@/components/stepper/DeploymentStepper";
import { ReleaseTab } from "@/components/release/ReleaseTab";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { deleteDeployment, getDeployment } from "@/lib/api";
import { useCan, useDeploymentPolling } from "@/lib/hooks";
import { appUrlOf } from "@/lib/utils";
import type { Deployment } from "@/types";
import {
    ArrowLeft,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    ExternalLink,
    FileCode,
    Globe,
    Loader2,
    RefreshCw,
    Settings,
    Shield,
    Trash2,
    Wallet,
    XCircle,
    BellRing,
    History,
    Rocket,
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

// Color indicator for the app status (green / orange / red, amber while in progress)
const STATUS_COLOR: Record<Deployment["status"], string> = {
    running: "bg-green-500",
    degraded: "bg-orange-500",
    failed: "bg-red-500",
    pending: "bg-amber-400",
    initializing: "bg-amber-400",
    planning: "bg-amber-400",
    deploying: "bg-amber-400",
    deleting: "bg-amber-400",
    deleted: "bg-muted-foreground",
};

const IN_PROGRESS_STATUSES = [
    "pending",
    "initializing",
    "planning",
    "deploying",
    "deleting",
];

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
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [deleteConfirmName, setDeleteConfirmName] = useState("");
    const [deleting, setDeleting] = useState(false);
    const [tab, setTab] = useState("app");
    // null = follow the status (collapsed once running) until the user toggles.
    const [statusOpen, setStatusOpen] = useState<boolean | null>(null);

    // Security findings link to "#security-data", which now lives in the
    // Configuration tab.
    useEffect(() => {
        if (window.location.hash === "#security-data") setTab("config");
    }, []);

    // The app's project comes from the route, so the role is available here
    // without an extra request.
    const { can } = useCan(projectName);
    const canDelete = can("app.delete");
    const canViewLogs = can("app.viewLogs");
    const canViewConfig = can("app.viewConfig");
    const canEditConfig = can("app.editConfig");
    const canViewFinops = can("finops.view");
    // Security, activity and alerts carry findings and logs: members and up.
    const canViewObservability = can("app.viewObservability");
    const canViewRelease = can("release.view");

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

    // The deployment row is kept (status=deleted), not removed, once
    // teardown finishes — polling correctly stops, but nothing else takes the
    // user off a page for an app that no longer exists. Bounce back to the
    // project after a beat. Watches `current`: after a delete started here,
    // the final "deleted" status only arrives through polling.
    useEffect(() => {
        if (current?.status !== "deleted") return;
        toast.info(`"${current.name}" has been deleted`);
        const timer = setTimeout(
            () => router.push(`/projects/${projectName}`),
            2500,
        );
        return () => clearTimeout(timer);
    }, [current?.status, current?.name, projectName, router]);

    // Show the inline health panel only for running/degraded apps
    const shouldFetchHealth =
        current?.status === "running" || current?.status === "degraded";

    const handleDelete = async () => {
        if (!canDelete) {
            toast.error("Only project admins can delete an application");
            setDeleteOpen(false);
            return;
        }
        if (!current || deleteConfirmName !== current.name) return;
        setDeleting(true);
        try {
            await deleteDeployment(appId);
            toast.success(`Deletion of "${current.name}" started`);
            setDeleteOpen(false);
            // Update local state to reflect deleting status — no page reload
            setDeployment((prev) =>
                prev ? { ...prev, status: "deleting", step_message: "🗑️ Destroying resources…" } : prev,
            );
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
    const appUrl = isRunning ? appUrlOf(current.terraform_outputs) : null;

    return (
        <div className="min-h-screen">
            {/* ── Header ── */}
            <header className="border-b px-6 py-4 flex items-center gap-3">
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
                            <div className="flex items-center gap-2.5">
                                <span
                                    title={current.status}
                                    className={`h-4 w-4 rounded-full shrink-0 ${STATUS_COLOR[current.status]} ${IN_PROGRESS_STATUSES.includes(current.status) ? "animate-pulse" : ""}`}
                                />
                                <span className="text-sm font-semibold capitalize text-muted-foreground">
                                    {current.status}
                                </span>
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
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={fetchDeployment}
                        >
                            <RefreshCw className="mr-2 h-4 w-4" />
                            Refresh
                        </Button>
                        {canDelete && (
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
                        )}
                    </div>
                </div>

                {isKubernetes && canViewObservability && (
                    <SecurityAlert
                        project={projectName}
                        app={current.name}
                        onOpen={() => setTab("security")}
                    />
                )}

                <Separator />

                <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
                <TabsList>
                    <TabsTrigger value="app" className="gap-2">
                        <FileCode className="h-4 w-4" />
                        Application
                    </TabsTrigger>
                    {isKubernetes && canViewRelease && (
                        <TabsTrigger value="release" className="gap-2">
                            <Rocket className="h-4 w-4" />
                            Release
                        </TabsTrigger>
                    )}
                    {isKubernetes && canViewObservability && (
                        <TabsTrigger value="security" className="gap-2">
                            <Shield className="h-4 w-4" />
                            Security
                        </TabsTrigger>
                    )}
                    {isKubernetes && canViewConfig && (
                        <TabsTrigger value="config" className="gap-2">
                            <Settings className="h-4 w-4" />
                            Configuration
                        </TabsTrigger>
                    )}
                    {canViewFinops && (
                        <TabsTrigger value="finops" className="gap-2">
                            <Wallet className="h-4 w-4" />
                            FinOps
                        </TabsTrigger>
                    )}
                    {isKubernetes && canViewObservability && (
                        <TabsTrigger value="activity" className="gap-2">
                            <History className="h-4 w-4" />
                            Activity
                        </TabsTrigger>
                    )}
                    {isKubernetes && canViewObservability && (
                        <TabsTrigger value="alerts" className="gap-2">
                            <BellRing className="h-4 w-4" />
                            Alerts
                        </TabsTrigger>
                    )}
                </TabsList>

                {/* Quick links — ArgoCD, Vault and the live app follow the
                    config/secrets permissions; the repo link is for everyone. */}
                {(() => {
                    const linkClass = buttonVariants({ variant: "outline", size: "sm" });
                    const showK8sLinks = isKubernetes && canViewLogs && current.project_id && current.name;
                    // Determine which ArgoCD app links to show based on template
                    const templateId = (current.template_id ?? "").toLowerCase();
                    const hasFrontend = templateId.includes("frontend") || templateId.includes("fullstack") || templateId.includes("full-stack") || templateId.includes("full_stack");
                    const hasBackend = !templateId.includes("frontend-only");
                    const base = `https://argocd.3istor.com/applications/argocd/${current.project_id}-${current.name}`;
                    const links = [
                        isKubernetes && canViewLogs && appUrl && { href: appUrl, icon: Globe, label: "Open Application" },
                        current.github_repo_url && { href: current.github_repo_url, icon: Github, label: "GitHub Repository" },
                        showK8sLinks && hasBackend && { href: `${base}-backend?resource=`, icon: FileCode, label: "ArgoCD — Backend" },
                        showK8sLinks && hasFrontend && { href: `${base}-frontend?resource=`, icon: FileCode, label: "ArgoCD — Frontend" },
                        // Vault is admin-only: only admins hold the project's
                        // Vault policy, so for anyone else this link is a 403.
                        showK8sLinks && can("app.viewSecretsLink") && {
                            href: `https://vault.3istor.com/ui/vault/secrets/kvv2/show/projects/${current.project_id}/${current.name}`,
                            icon: Shield,
                            label: "Manage Secrets in Vault",
                        },
                    ].filter((l) => !!l);
                    if (links.length === 0) return null;
                    return (
                        <div className="mt-4 flex items-center gap-2 flex-wrap">
                            <span className="text-xs font-medium text-muted-foreground mr-1">
                                Quick links
                            </span>
                            {links.map(({ href, icon: Icon, label }) => (
                                <a
                                    key={label}
                                    href={href}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className={linkClass}
                                >
                                    <Icon className="mr-2 h-4 w-4" />
                                    {label}
                                    <ExternalLink className="ml-2 h-3 w-3" />
                                </a>
                            ))}
                        </div>
                    );
                })()}

                <TabsContent value="app" className="mt-6 space-y-6">
                    {/* Deployment progress — collapsed by default once running */}
                    <Card>
                        <CardHeader className="flex flex-row items-center justify-between">
                            <CardTitle className="text-base">Deployment Status</CardTitle>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => setStatusOpen(!(statusOpen ?? !isRunning))}
                                className="h-8 w-8"
                                title={(statusOpen ?? !isRunning) ? "Collapse" : "Expand"}
                            >
                                {(statusOpen ?? !isRunning) ? (
                                    <ChevronUp className="h-4 w-4" />
                                ) : (
                                    <ChevronDown className="h-4 w-4" />
                                )}
                            </Button>
                        </CardHeader>
                        {(statusOpen ?? !isRunning) && (
                            <CardContent>
                                <DeploymentStepper deployment={current} />
                            </CardContent>
                        )}
                    </Card>

                    {/* Live deployment log streaming — members and up.
                        A guest sees the status badge and the stepper, not
                        the build output. */}
                    {canViewLogs && (
                        <DeploymentLogs deploymentId={appId} deploymentStatus={current.status} />
                    )}

                    {/* Health summary (inline, collapsible like the logs) */}
                    {shouldFetchHealth && canViewLogs && (
                        <DeploymentHealth deploymentId={appId} />
                    )}

                    {/* Legacy deployments: show Terraform outputs */}
                    {!isKubernetes && canViewConfig && (
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
                </TabsContent>

                {/* Day-2 config — members see the values read-only, admins
                    can change them, guests get no tab at all. */}
                {isKubernetes && canViewConfig && (
                    <TabsContent value="config" className="mt-6">
                        {isRunning ? (
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
                                <div id="security-data" className="scroll-mt-24">
                                    <SecurityDataPanel deploymentId={appId} />
                                </div>
                                <AppConfigPanel
                                    deploymentId={appId}
                                    readOnly={!canEditConfig}
                                />
                            </div>
                        ) : (
                            <Card>
                                <CardContent className="pt-6">
                                    <p className="text-sm text-muted-foreground text-center">
                                        Configuration will be available once the application is{" "}
                                        <span className="font-medium text-foreground">running</span>.
                                    </p>
                                </CardContent>
                            </Card>
                        )}
                    </TabsContent>
                )}

                {isKubernetes && canViewRelease && (
                    <TabsContent value="release" className="mt-6">
                        <ReleaseTab deploymentId={appId} projectName={projectName} />
                    </TabsContent>
                )}

                {isKubernetes && canViewObservability && (
                    <TabsContent value="security" className="mt-6">
                        <SecurityDashboard
                            project={projectName}
                            app={current.name}
                            fullPageLink
                        />
                    </TabsContent>
                )}

                {isKubernetes && canViewObservability && (
                    <TabsContent value="activity" className="mt-6">
                        <ActivityTab project={projectName} app={current.name} />
                    </TabsContent>
                )}

                {isKubernetes && canViewObservability && (
                    <TabsContent value="alerts" className="mt-6">
                        <AlertsPanel project={projectName} app={current.name} />
                    </TabsContent>
                )}

                {canViewFinops && (
                    <TabsContent value="finops" className="mt-6 space-y-3">
                        <p className="text-sm text-muted-foreground">
                            Les coûts sont suivis au niveau du projet.
                        </p>
                        <ProjectFinopsPanel project={projectName} />
                    </TabsContent>
                )}
                </Tabs>
            </main>

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
                            <span className="font-semibold">&quot;{current.name}&quot;</span> via
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
