"use client";

import { ClipboardCopy, ShieldAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/components/activity/labels";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, getIssueReports, setIssueReportStatus } from "@/lib/api";
import { useFormat } from "@/lib/useFormat";
import type { IssueReport, IssueReportStatus } from "@/types/issueReport";

const FILTERS: (IssueReportStatus | "all")[] = ["new", "exported", "closed", "all"];

const STATUS_VARIANT: Record<IssueReportStatus, "default" | "secondary" | "outline"> = {
  new: "default",
  exported: "outline",
  closed: "secondary",
};

// Pasted by hand into a Jira ticket: Jira renders this Markdown.
function asJiraText(report: IssueReport, categoryLabel: string): string {
  return [
    `**${report.summary}**`,
    "",
    `- Catégorie : ${categoryLabel}`,
    report.tool ? `- Outil MCP : ${report.tool}` : null,
    report.project ? `- Projet : ${report.project}` : null,
    `- Signalé ${report.occurrences} fois, par ${report.reporters.join(", ")}`,
    report.assistant ? `- Assistant : ${report.assistant}` : null,
    report.steps ? `\n${report.steps}` : null,
    `\nRetour CMP n°${report.id}`,
  ]
    .filter((line) => line !== null)
    .join("\n");
}

export function IssueReportsPanel() {
  const t = useTranslations("issueReports");
  const format = useFormat();
  const [filter, setFilter] = useState<IssueReportStatus | "all">("new");
  const [reports, setReports] = useState<IssueReport[] | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    getIssueReports(filter === "all" ? undefined : filter)
      .then((rows) => {
        setReports(rows);
        setForbidden(false);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else toast.error(errorMessage(err));
      });
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  const setStatus = async (report: IssueReport, status: IssueReportStatus) => {
    setBusy(report.id);
    try {
      await setIssueReportStatus(report.id, status);
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const copyForJira = async (report: IssueReport) => {
    await navigator.clipboard.writeText(
      asJiraText(report, t(`categories.${report.category}`)),
    );
    toast.success(t("copied"));
  };

  if (forbidden) {
    return (
      <Card>
        <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
          <ShieldAlert className="h-4 w-4" aria-hidden />
          {t("forbidden")}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("filterLabel")}>
        {FILTERS.map((value) => (
          <Button
            key={value}
            size="sm"
            variant={filter === value ? "default" : "outline"}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {t(`filters.${value}`)}
          </Button>
        ))}
      </div>

      {reports === null ? (
        <ul className="space-y-3" aria-busy aria-label={t("loading")}>
          {[0, 1, 2].map((i) => (
            <li key={i}>
              <Card>
                <CardContent className="space-y-3">
                  <Skeleton className="h-5 w-64" />
                  <Skeleton className="h-5 w-full max-w-lg" />
                  <Skeleton className="h-4 w-80" />
                  <div className="flex gap-2">
                    <Skeleton className="h-8 w-32" />
                    <Skeleton className="h-8 w-28" />
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      ) : reports.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <ul className="space-y-3">
          {reports.map((report) => (
            <li key={report.id}>
              <Card>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={STATUS_VARIANT[report.status]}>
                      {t(`statuses.${report.status}`)}
                    </Badge>
                    <Badge variant="outline">{t(`categories.${report.category}`)}</Badge>
                    {report.tool && <Badge variant="secondary">{report.tool}</Badge>}
                    <span className="text-xs text-muted-foreground">
                      {t("seen", {
                        count: report.occurrences,
                        date: format.dateTime(report.last_reported_at),
                      })}
                    </span>
                  </div>
                  <p className="font-medium">{report.summary}</p>
                  {report.steps && (
                    <pre className="whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs">
                      {report.steps}
                    </pre>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {t("reportedBy", {
                      users: report.reporters.join(", "),
                      assistant: report.assistant ?? t("unknownAssistant"),
                    })}
                    {report.project && ` · ${t("project", { project: report.project })}`}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={() => copyForJira(report)}>
                      <ClipboardCopy className="h-4 w-4" aria-hidden />
                      {t("copyForJira")}
                    </Button>
                    {report.status !== "exported" && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy === report.id}
                        onClick={() => setStatus(report, "exported")}
                      >
                        {t("markExported")}
                      </Button>
                    )}
                    {report.status !== "closed" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === report.id}
                        onClick={() => setStatus(report, "closed")}
                      >
                        {t("close")}
                      </Button>
                    )}
                    {report.status === "closed" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === report.id}
                        onClick={() => setStatus(report, "new")}
                      >
                        {t("reopen")}
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
