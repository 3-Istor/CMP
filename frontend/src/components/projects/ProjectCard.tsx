"use client";

import { Badge } from "@/components/ui/badge";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { GROUP_SUFFIX, ROLE_BLURB } from "@/lib/permissions";
import type { Project, ProjectRole } from "@/types";
import {
    Crown,
    Eye,
    FolderKanban,
    Loader2,
    ShieldCheck,
    Users,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

interface Props {
    project: Project;
    /** Epoch ms when the delete was confirmed, if a teardown is in flight. */
    deletingSince?: number;
}

const ROLE_ICON: Record<ProjectRole, typeof Crown> = {
    owner: Crown,
    admin: ShieldCheck,
    member: Users,
    guest: Eye,
};

// Same asymptotic-progress trick as PendingProjectCard, so the bar resumes
// correctly across a reload instead of restarting at zero.
const START = 8;
const CEILING = 92;
const TAU_MS = 20000;

function progressFor(since: number): number {
    const elapsed = Math.max(0, Date.now() - since);
    const value = CEILING - (CEILING - START) * Math.exp(-elapsed / TAU_MS);
    return Math.min(CEILING, Math.round(value));
}

export function ProjectCard({ project, deletingSince }: Props) {
    const isOwner = project.role === "owner";
    const isAdmin = project.role === "admin" || isOwner;
    const RoleIcon = ROLE_ICON[project.role] ?? Users;
    const isDeleting = deletingSince !== undefined;

    const [progress, setProgress] = useState(() =>
        deletingSince ? progressFor(deletingSince) : 0,
    );

    useEffect(() => {
        if (!deletingSince) return;
        const timer = setInterval(() => {
            setProgress(progressFor(deletingSince));
        }, 1000);
        return () => clearInterval(timer);
    }, [deletingSince]);

    const cardBody = (
        <Card
            className={
                isDeleting
                    ? "h-full border-destructive/30 opacity-70"
                    : "h-full transition-shadow group-hover:shadow-md group-hover:border-primary/30"
            }
        >
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                    <div className="rounded-lg bg-primary/10 p-2.5">
                        <FolderKanban className="h-5 w-5 text-primary" />
                    </div>
                    {isDeleting ? (
                        <Badge variant="destructive" className="shrink-0 gap-1">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Deleting
                        </Badge>
                    ) : (
                        <Badge
                            variant={
                                isAdmin
                                    ? "default"
                                    : project.role === "guest"
                                      ? "outline"
                                      : "secondary"
                            }
                            className={`shrink-0 ${isOwner ? "bg-amber-500 hover:bg-amber-500 text-white" : ""}`}
                        >
                            <RoleIcon className="mr-1 h-3 w-3" />
                            {project.role}
                        </Badge>
                    )}
                </div>
                <CardTitle className="text-base mt-2 capitalize">
                    {project.name}
                </CardTitle>
                <CardDescription className="text-xs">
                    {isDeleting
                        ? "Tearing down project resources…"
                        : `Kubernetes project · ${ROLE_BLURB[project.role]}`}
                </CardDescription>
            </CardHeader>
            <CardContent className="pt-0 space-y-1.5">
                {isDeleting ? (
                    <>
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                            <span className="animate-pulse">
                                Keycloak · Vault · ArgoCD
                            </span>
                            <span className="tabular-nums font-mono">{progress}%</span>
                        </div>
                        <Progress value={progress} className="h-1.5 [&>div]:bg-destructive" />
                    </>
                ) : (
                    <div className="text-xs text-muted-foreground font-mono bg-muted/50 rounded px-2 py-1">
                        project-{project.name}-{GROUP_SUFFIX[project.role]}
                    </div>
                )}
            </CardContent>
        </Card>
    );

    // Not a Link while deleting: the project is on its way out, and its
    // detail page's admin-group access check can fail mid-teardown anyway.
    if (isDeleting) {
        return (
            <div
                className="block cursor-not-allowed select-none"
                title="This project is being deleted"
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
