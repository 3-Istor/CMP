"use client";

import { useTranslations } from "next-intl";

export default function SecurityLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("security.layout");
  return (
    <div className="min-h-screen">
      <header className="border-b px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">{t("title")}</h1>
            <p className="text-xs text-muted-foreground">{t("subtitle")}</p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
