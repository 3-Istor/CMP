"use client";

import { Badge } from "@/components/ui/badge";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

export function DeletingBadge() {
  const t = useTranslations("appPage.deploymentStatus");
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      {t("deleting")}
    </Badge>
  );
}
