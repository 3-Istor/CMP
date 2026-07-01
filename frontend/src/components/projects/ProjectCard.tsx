"use client";

import { Badge } from "@/components/ui/badge";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import type { Project } from "@/types";
import { Crown, FolderKanban, ShieldCheck, Users } from "lucide-react";
import Link from "next/link";

interface Props {
    project: Project;
}

export function ProjectCard({ project }: Props) {
    const isOwner = project.role === "owner";
    const isAdmin = project.role === "admin" || isOwner;

    return (
        <Link href={`/projects/${project.name}`} className="group block">
            <Card className="h-full transition-shadow group-hover:shadow-md group-hover:border-primary/30">
                <CardHeader className="pb-3">
                    <div className="flex items-start justify-between gap-2">
                        <div className="rounded-lg bg-primary/10 p-2.5">
                            <FolderKanban className="h-5 w-5 text-primary" />
                        </div>
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
                    </div>
                    <CardTitle className="text-base mt-2 capitalize">
                        {project.name}
                    </CardTitle>
                    <CardDescription className="text-xs">
                        Kubernetes project · {isOwner ? "Owner" : isAdmin ? "Full access" : "Read & deploy"}
                    </CardDescription>
                </CardHeader>
                <CardContent className="pt-0">
                    <div className="text-xs text-muted-foreground font-mono bg-muted/50 rounded px-2 py-1">
                        project-{project.name}-{isAdmin ? "admins" : "members"}
                    </div>
                </CardContent>
            </Card>
        </Link>
    );
}
