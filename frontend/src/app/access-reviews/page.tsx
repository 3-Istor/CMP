"use client";

import { UserCheck } from "lucide-react";
import { useTranslations } from "next-intl";

import { AccessReviewsPanel } from "@/components/access-reviews/AccessReviewsPanel";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

export default function AccessReviewsPage() {
  const t = useTranslations("accessReviews");

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={t("description")}
        icon={UserCheck}
      />
      <PageBody>
        <AccessReviewsPanel />
      </PageBody>
    </div>
  );
}
