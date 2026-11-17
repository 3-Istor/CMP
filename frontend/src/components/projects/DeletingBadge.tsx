"use client";

import { Badge } from "@/components/ui/badge";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

export function ProgressBadge({ label }: { label: string }) {
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      {label}
    </Badge>
  );
}

export function DeletingBadge() {
  const t = useTranslations("appPage.deploymentStatus");
  return <ProgressBadge label={t("deleting")} />;
}
