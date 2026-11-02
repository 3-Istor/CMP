"use client";

import { useTranslations } from "next-intl";

export function NotConnected({ cloud }: { cloud: string }) {
  const t = useTranslations("clusters.notConnected");
  return (
    <div className="rounded-lg border border-dashed px-4 py-6 text-sm">
      <p className="font-medium">{t("title", { cloud: cloud.toUpperCase() })}</p>
      <p className="text-muted-foreground">{t("body")}</p>
    </div>
  );
}
