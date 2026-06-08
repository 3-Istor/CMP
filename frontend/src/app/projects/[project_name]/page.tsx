"use client";

import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { UserNav } from "@/components/layout/UserNav";
import { AppCard } from "@/components/projects/AppCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { createDeployment, getCatalog } from "@/lib/api";
import { useProjectApps } from "@/lib/hooks";
import type { CatalogTemplate } from "@/types";
import {
    ArrowLeft,
    FolderKanban,
    LayoutGrid,
    Loader2,
    Plus,
    RefreshCw,
    ShieldCheck,
    Users,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

export default function ProjectPage() {
    const params = useParams();
    const router = useRouter();
    const projectName = params.project_name as string;

    const { apps, loading: loadingApps, error: appsError, refresh: refreshApps } =
        useProjectApps(projectName);

    // Catalog for deploy modal
    const [templates, setTemplates] = useState<CatalogTemplate[]>([]);
    const [loadingCatalog, setLoadingCatalog] = useState(false);
    const [selectedTemplate, setSelectedTemplate] =
        useState<CatalogTemplate | null>(null);
    const [deploying, setDeploying] = useState(false);
    const [showCatalog, setShowCatalog] = useState(false);

    const paasTemplates = templates.filter((t) => t.category === "paas");

    const loadCatalog = async () => {
        if (templates.length > 0) return; // Already loaded
        setLoadingCatalog(true);
        try {
            const data = await getCatalog();
            setTemplates(data);
        } catch {
            toast.error("Failed to load templates");
        } finally {
            setLoadingCatalog(false);
        }
    };

    const handleOpenCatalog = () => {
        setShowCatalog(true);
        loadCatalog();
    };

    const handleDeploy = async (
        name: string,
        config: Record<string, string | number>,
    ) => {
        if (!selectedTemplate) return;
        setDeploying(true);
        try {
            await createDeployment({
                name,
                template_id: selectedTemplate.id,
                // Inject project context so the backend stores project_id
                app_config: { ...config, project_name: projectName },
            });
            toast.success(`Deployment of "${name}" started`);
            setSelectedTemplate(null);
            setShowCatalog(false);
            refreshApps();
        } catch (err) {
            toast.error(`Deploy failed: ${err}`);
        } finally {
            setDeploying(false);
        }
    };

    // Derive member list from app metadata (Keycloak members would need a separate API —
    // for now we display a placeholder)
    const membersPlaceholder = [
        { name: "You (current user)", role: "admin" as const },
    ];

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

            <main className="px-6 py-8 max-w-7xl mx-auto space-y-6">
                {/* ── Breadcrumb & title ── */}
                <div className="space-y-3">
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => router.push("/")}
                        className="-ml-2"
                    >
                        <ArrowLeft className="mr-2 h-4 w-4" />
                        All Projects
                    </Button>

                    <div className="flex items-center justify-between gap-4 flex-wrap">
                        <div className="flex items-center gap-3">
                            <div className="rounded-lg bg-primary/10 p-2.5">
                                <FolderKanban className="h-6 w-6 text-primary" />
                            </div>
                            <div>
                                <h2 className="text-2xl font-bold capitalize">{projectName}</h2>
                                <p className="text-sm text-muted-foreground">
                                    Kubernetes project
                                </p>
                            </div>
                        </div>
                        <Badge variant="outline" className="font-mono text-xs">
                            {apps.length} app{apps.length !== 1 ? "s" : ""}
                        </Badge>
                    </div>
                </div>

                <Separator />

                {/* ── Tabs ── */}
                <Tabs defaultValue="apps">
                    <TabsList>
                        <TabsTrigger value="apps" className="gap-2">
                            <LayoutGrid className="h-4 w-4" />
                            Applications
                        </TabsTrigger>
                        <TabsTrigger value="members" className="gap-2">
                            <Users className="h-4 w-4" />
                            Members
                        </TabsTrigger>
                    </TabsList>

                    {/* ── Applications tab ── */}
                    <TabsContent value="apps" className="mt-6 space-y-6">
                        <div className="flex items-center justify-between gap-4">
                            <p className="text-sm text-muted-foreground">
                                Applications deployed in this project.
                            </p>
                            <div className="flex items-center gap-2">
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={refreshApps}
                                    disabled={loadingApps}
                                >
                                    <RefreshCw
                                        className={`h-4 w-4 ${loadingApps ? "animate-spin" : ""}`}
                                    />
                                </Button>
                                <Button size="sm" onClick={handleOpenCatalog}>
                                    <Plus className="mr-2 h-4 w-4" />
                                    Deploy App
                                </Button>
                            </div>
                        </div>

                        {/* Inline catalog (shown when user clicks "Deploy App") */}
                        {showCatalog && (
                            <div className="rounded-xl border bg-card p-6 space-y-4">
                                <div className="flex items-center justify-between">
                                    <h3 className="font-semibold">
                                        Select a Kubernetes template
                                    </h3>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => setShowCatalog(false)}
                                    >
                                        Close
                                    </Button>
                                </div>
                                {loadingCatalog ? (
                                    <div className="flex items-center gap-2 text-muted-foreground py-4">
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Loading templates…
                                    </div>
                                ) : paasTemplates.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">
                                        No Kubernetes templates available.
                                    </p>
                                ) : (
                                    <CatalogGrid
                                        templates={paasTemplates}
                                        onDeploy={(t) => {
                                            setSelectedTemplate(t);
                                            setShowCatalog(false);
                                        }}
                                    />
                                )}
                            </div>
                        )}

                        {/* Apps grid */}
                        {loadingApps ? (
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                                {[1, 2, 3].map((i) => (
                                    <Skeleton key={i} className="h-40 rounded-xl" />
                                ))}
                            </div>
                        ) : appsError ? (
                            <div className="rounded-xl border border-destructive bg-destructive/5 p-6 text-destructive space-y-2">
                                <p className="font-medium">Failed to load applications</p>
                                <p className="text-sm opacity-80">{appsError}</p>
                                <Button variant="outline" size="sm" onClick={refreshApps}>
                                    Retry
                                </Button>
                            </div>
                        ) : apps.length === 0 ? (
                            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-muted-foreground">
                                <span className="text-4xl mb-3">🚀</span>
                                <p className="font-medium">No applications yet</p>
                                <p className="text-sm mt-1">
                                    Deploy your first app to this project.
                                </p>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="mt-4"
                                    onClick={handleOpenCatalog}
                                >
                                    <Plus className="mr-2 h-4 w-4" />
                                    Deploy App
                                </Button>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                                {apps.map((app) => (
                                    <AppCard
                                        key={app.id}
                                        app={app}
                                        projectName={projectName}
                                    />
                                ))}
                            </div>
                        )}
                    </TabsContent>

                    {/* ── Members tab ── */}
                    <TabsContent value="members" className="mt-6">
                        <div className="space-y-4">
                            <p className="text-sm text-muted-foreground">
                                Members are managed via Keycloak groups{" "}
                                <span className="font-mono">
                                    project-{projectName}-admins
                                </span>{" "}
                                and{" "}
                                <span className="font-mono">
                                    project-{projectName}-members
                                </span>
                                .
                            </p>

                            <div className="rounded-xl border divide-y divide-border">
                                {membersPlaceholder.map((m, i) => (
                                    <div
                                        key={i}
                                        className="flex items-center justify-between px-4 py-3"
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center">
                                                <Users className="h-4 w-4 text-muted-foreground" />
                                            </div>
                                            <span className="text-sm font-medium">{m.name}</span>
                                        </div>
                                        <Badge
                                            variant={m.role === "admin" ? "default" : "secondary"}
                                        >
                                            {m.role === "admin" ? (
                                                <ShieldCheck className="mr-1 h-3 w-3" />
                                            ) : (
                                                <Users className="mr-1 h-3 w-3" />
                                            )}
                                            {m.role}
                                        </Badge>
                                    </div>
                                ))}
                            </div>

                            <p className="text-xs text-muted-foreground">
                                To add members, assign them to the appropriate Keycloak group
                                via the{" "}
                                <a
                                    href="https://auth.3istor.com/admin"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="underline hover:no-underline"
                                >
                                    Keycloak Admin Console
                                </a>
                                .
                            </p>
                        </div>
                    </TabsContent>
                </Tabs>
            </main>

            {/* ── Deploy modal ── */}
            <DeployModal
                template={selectedTemplate}
                onClose={() => setSelectedTemplate(null)}
                onConfirm={handleDeploy}
                loading={deploying}
            />
        </div>
    );
}
