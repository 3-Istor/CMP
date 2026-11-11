"use client";

import { KeyRound } from "lucide-react";
import { useTranslations } from "next-intl";

import { AccessPanel } from "@/components/access/AccessPanel";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

export default function AccessPage() {
  const t = useTranslations("access");

  return (
    <div className="min-h-screen">
      <PageHeader
        title={t("title")}
        description={t("description")}
        icon={KeyRound}
      />
      <PageBody>
        <AccessPanel />
      </PageBody>
    </div>
  );
}
