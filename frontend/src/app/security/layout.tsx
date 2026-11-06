"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Shield } from "lucide-react";
import { useTranslations } from "next-intl";

export default function SecurityLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("security.layout");
  return (
    <div className="min-h-screen">
      <PageHeader title={t("title")} description={t("subtitle")} icon={Shield} />
      <PageBody>{children}</PageBody>
    </div>
  );
}
