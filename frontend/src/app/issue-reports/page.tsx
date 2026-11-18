"use client";

import { MessageSquareWarning } from "lucide-react";
import { useTranslations } from "next-intl";

import { IssueReportsPanel } from "@/components/issue-reports/IssueReportsPanel";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

export default function IssueReportsPage() {
  const t = useTranslations("issueReports");

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={t("description")}
        icon={MessageSquareWarning}
      />
      <PageBody>
        <IssueReportsPanel />
      </PageBody>
    </div>
  );
}
