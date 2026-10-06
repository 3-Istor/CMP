"use client";

import { Badge } from "@/components/ui/badge";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { createProject, deleteProject } from "@/lib/api";
import type { Project } from "@/types";
import { isProjectFailed, isProjectInFlight } from "@/types";
import {
    AlertTriangle,
    Crown,
    FolderKanban,
    Loader2,
    RotateCw,
    ShieldCheck,
    Users,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

interface Props {
    project: Project;
    /** Called after a retry was accepted, so the list picks up the new status. */
    onRetried?: () => void;
}

export function ProjectCard({ project, onRetried }: Props) {
    const [retrying, setRetrying] = useState(false);
    const isOwner = project.role === "owner";
    const isAdmin = project.role === "admin" || isOwner;
    const isCreating = project.status === "provisioning";
    const isDeleting = project.status === "decommissioning";
    const isFailed = isProjectFailed(project);
    const isInFlight = isProjectInFlight(project);

    // A failed bootstrap is retried by creating the project again, a failed
    // teardown by deleting it again. The backend resumes from what is left.
    const retry = async () => {
        setRetrying(true);
        try {
            if (project.status === "decommission_failed") {
                await deleteProject(project.name);
            } else {
                await createProject(project.name, project.target_cloud);
            }
            onRetried?.();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            toast.error(`Retry failed: ${msg}`);
        } finally {
            setRetrying(false);
        }
    };

    const cardBody = (
        <Card
            className={
                isDeleting
                    ? "h-full border-destructive/30 opacity-70"
                    : isFailed
                      ? "h-full border-destructive/50"
                      : isCreating
                        ? "h-full border-primary/30"
                        : "h-full transition-shadow group-hover:shadow-md group-hover:border-primary/30"
            }
        >
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                    <div className="rounded-lg bg-primary/10 p-2.5">
                        <FolderKanban className="h-5 w-5 text-primary" />
                    </div>
                    {isCreating ? (
                        <Badge variant="outline" className="shrink-0 gap-1">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Creating
                        </Badge>
                    ) : isDeleting ? (
                        <Badge variant="destructive" className="shrink-0 gap-1">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Deleting
                        </Badge>
                    ) : isFailed ? (
                        <Badge variant="destructive" className="shrink-0 gap-1">
                            <AlertTriangle className="h-3 w-3" />
                            Failed
                        </Badge>
                    ) : (
                        <Badge
                            variant={isAdmin ? "default" : "secondary"}
                            className={`shrink-0 ${isOwner ? "bg-amber-500 hover:bg-amber-500 text-white" : ""}`}
                        >
                            {isOwner ? (
                                <Crown className="mr-1 h-3 w-3" />
                            ) : project.role === "admin" ? (
                                <ShieldCheck className="mr-1 h-3 w-3" />
                            ) : (
                                <Users className="mr-1 h-3 w-3" />
                            )}
                            {project.role}
                        </Badge>
                    )}
                </div>
                <CardTitle className="text-base mt-2 capitalize">
                    {project.name}
                </CardTitle>
                <CardDescription className="text-xs">
                    {`Kubernetes project · ${isOwner ? "Owner" : isAdmin ? "Full access" : "Read & deploy"}`}
                </CardDescription>
            </CardHeader>
            <CardContent className="pt-0 space-y-1.5">
                {isInFlight ? (
                    <>
                        <p className="text-xs text-muted-foreground">
                            {project.step_message}
                        </p>
                        <Progress
                            value={null}
                            className={`h-1.5 ${isDeleting ? "[&>div]:bg-destructive" : ""}`}
                        />
                    </>
                ) : isFailed ? (
                    <>
                        <p className="text-xs text-destructive">
                            {project.step_message}
                        </p>
                        <Button
                            size="sm"
                            variant="outline"
                            className="mt-1 cursor-pointer"
                            disabled={retrying}
                            onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                void retry();
                            }}
                        >
                            {retrying ? (
                                <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                            ) : (
                                <RotateCw className="mr-2 h-3 w-3" />
                            )}
                            Retry
                        </Button>
                    </>
                ) : (
                    <div className="text-xs text-muted-foreground font-mono bg-muted/50 rounded px-2 py-1">
                        project-{project.name}-{isAdmin ? "admins" : "members"}
                    </div>
                )}
            </CardContent>
        </Card>
    );

    // Not a Link while a bootstrap or teardown runs: the Keycloak groups that
    // back project access are being created or removed, so the detail page can
    // fail its access check. The same holds for a failed bootstrap, which never
    // created them.
    if (isInFlight || !project.is_accessible) {
        return (
            <div
                className="block cursor-not-allowed select-none"
                title={
                    isDeleting
                        ? "This project is being deleted"
                        : isCreating
                          ? "This project is still being created"
                          : project.status === "decommission_failed"
                            ? "This project could not be deleted"
                            : "This project could not be created"
                }
                aria-disabled="true"
            >
                {cardBody}
            </div>
        );
    }

    return (
        <Link href={`/projects/${project.name}`} className="group block">
            {cardBody}
        </Link>
    );
}
