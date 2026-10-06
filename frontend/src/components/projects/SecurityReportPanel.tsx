"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getProjectSecurity } from "@/lib/api";
import type {
  ProjectSecurityReport,
  SecurityControl,
  SecurityStatus,
} from "@/types";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const STATUS: Record<SecurityStatus, { label: string; className: string }> = {
  ok: {
    label: "OK",
    className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  },
  warn: {
    label: "À vérifier",
    className: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  },
  fail: {
    label: "À corriger",
    className: "bg-red-500/15 text-red-700 dark:text-red-300",
  },
  unknown: {
    label: "Non mesuré",
    className: "bg-muted text-muted-foreground",
  },
};

function StatusBadge({ status }: { status: SecurityStatus }) {
  const { label, className } = STATUS[status];
  return (
    <Badge variant="outline" className={`border-transparent ${className}`}>
      {label}
    </Badge>
  );
}

function ControlRow({ control }: { control: SecurityControl }) {
  const [open, setOpen] = useState(false);
  const expandable = control.findings.length > 0;
  return (
    <div className="border-b last:border-b-0">
      <button
        type="button"
        className="flex w-full items-start gap-3 py-3 text-left disabled:cursor-default"
        onClick={() => setOpen((v) => !v)}
        disabled={!expandable}
        aria-expanded={open}
      >
        <span className="mt-0.5 w-4 shrink-0 text-muted-foreground">
          {expandable &&
            (open ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            ))}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium">{control.title}</span>
          <span className="block text-sm text-muted-foreground">
            {control.description}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1">
          <StatusBadge status={control.status} />
          <span className="text-xs text-muted-foreground">
            {control.summary}
          </span>
        </span>
      </button>
      {open && expandable && (
        <ul className="mb-3 ml-7 space-y-1.5">
          {control.findings.map((finding) => (
            <li
              key={finding.subject}
              className="flex items-start justify-between gap-3 text-sm"
            >
              <span className="min-w-0">
                <span className="font-mono text-xs">{finding.subject}</span>
                <span className="block break-words text-muted-foreground">
                  {finding.detail}
                </span>
              </span>
              <StatusBadge status={finding.status} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SecurityReportPanel({ projectName }: { projectName: string }) {
  const [report, setReport] = useState<ProjectSecurityReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await getProjectSecurity(projectName));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectName]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !report) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (error && !report) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Rapport de sécurité indisponible : {error}
        </CardContent>
      </Card>
    );
  }
  if (!report) return null;

  const measured = report.controls.filter((c) => c.status !== "unknown");
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Sécurité du projet</CardTitle>
            <CardDescription>
              {measured.length} contrôle{measured.length !== 1 ? "s" : ""}{" "}
              mesuré{measured.length !== 1 ? "s" : ""} sur{" "}
              {report.controls.length}. Mis à jour{" "}
              {new Date(report.generated_at).toLocaleTimeString("fr-FR")}.
            </CardDescription>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-3xl font-semibold tabular-nums">
              {report.score === null ? "–" : `${report.score}%`}
            </span>
            <Button
              variant="outline"
              size="icon"
              onClick={load}
              disabled={loading}
              aria-label="Rafraîchir"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {report.controls.map((control) => (
            <ControlRow key={control.id} control={control} />
          ))}
        </CardContent>
      </Card>

      {report.platform_controls.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Composants de la plateforme</CardTitle>
            <CardDescription>
              Pods gérés par la plateforme dans le namespace système du projet.
              Hors score : leur correction revient à l&apos;équipe plateforme.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {report.platform_controls.map((control) => (
              <ControlRow key={control.id} control={control} />
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
