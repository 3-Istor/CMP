"use client";

import { Badge } from "@/components/ui/badge";
import {
    Card,
    CardContent,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import type { Deployment } from "@/types";
import {
    CheckCircle2,
    Clock,
    GitBranch,
    Loader2,
    XCircle,
} from "lucide-react";
import Link from "next/link";

interface Props {
    app: Deployment;
    projectName: string;
}

function StatusIcon({ status }: { status: Deployment["status"] }) {
    if (status === "running")
        return <CheckCircle2 className="h-4 w-4 text-green-500" />;
    if (status === "failed" || status === "deleted")
        return <XCircle className="h-4 w-4 text-destructive" />;
    if (
        ["pending", "initializing", "planning", "deploying", "deleting"].includes(
            status,
        )
    )
        return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
    return <Clock className="h-4 w-4 text-muted-foreground" />;
}

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

export function AppCard({ app, projectName }: Props) {
    return (
        <Link
            href={`/projects/${projectName}/apps/${app.id}`}
            className="group block"
        >
            <Card className="h-full transition-shadow group-hover:shadow-md group-hover:border-primary/30">
                <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                            {app.template_icon && (
                                <span className="text-2xl">{app.template_icon}</span>
                            )}
                            <CardTitle className="text-base leading-tight">
                                {app.name}
                            </CardTitle>
                        </div>
                        <Badge
                            variant={STATUS_VARIANT[app.status]}
                            className="shrink-0 capitalize"
                        >
                            <StatusIcon status={app.status} />
                            <span className="ml-1">{app.status}</span>
                        </Badge>
                    </div>
                </CardHeader>
                <CardContent className="space-y-2 pt-0">
                    <p className="text-xs text-muted-foreground">
                        {app.template_name || app.template_id}
                    </p>
                    {app.github_repo_url && (
                        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            <GitBranch className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate font-mono">
                                {app.github_repo_url.replace("https://github.com/", "")}
                            </span>
                        </div>
                    )}
                    {app.k8s_namespace && (
                        <div className="rounded bg-muted/50 px-2 py-0.5 text-xs font-mono text-muted-foreground">
                            ns: {app.k8s_namespace}
                        </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                        {new Date(app.created_at).toLocaleDateString(undefined, {
                            day: "numeric",
                            month: "short",
                            year: "numeric",
                        })}
                    </p>
                </CardContent>
            </Card>
        </Link>
    );
}
