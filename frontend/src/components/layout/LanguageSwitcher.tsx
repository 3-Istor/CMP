"use client";

import { Button } from "@/components/ui/button";
import { updatePreferences } from "@/lib/api";
import { LOCALES, type Locale } from "@/i18n/config";
import { setLocaleCookie } from "@/i18n/cookie";
import { cn } from "@/lib/utils";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { toast } from "sonner";

export function LanguageSwitcher({ className }: { className?: string }) {
  const t = useTranslations("common.language");
  const current = useLocale();
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [saving, setSaving] = useState(false);

  const choose = async (locale: Locale) => {
    if (locale === current || saving) return;
    setSaving(true);
    setLocaleCookie(locale);
    try {
      await updatePreferences({ locale });
    } catch {
      toast.error(t("saveFailed"));
    } finally {
      setSaving(false);
    }
    startTransition(() => router.refresh());
  };

  return (
    <div
      role="group"
      aria-label={t("label")}
      className={cn("flex gap-2", className)}
    >
      {LOCALES.map((locale) => (
        <Button
          key={locale}
          type="button"
          size="sm"
          variant={current === locale ? "default" : "outline"}
          aria-pressed={current === locale}
          disabled={pending || saving}
          onClick={() => choose(locale)}
          lang={locale}
        >
          {t(`names.${locale}`)}
        </Button>
      ))}
    </div>
  );
}
