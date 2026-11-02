"use client";

import { useTheme, type Theme } from "@/lib/theme";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useTranslations } from "next-intl";

const OPTIONS: Theme[] = ["system", "light", "dark"];

export function ThemeCard() {
  const t = useTranslations("account");
  const [theme, choose] = useTheme();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("theme.title")}</CardTitle>
        <CardDescription>{t("theme.description")}</CardDescription>
      </CardHeader>
      <CardContent className="flex gap-2">
        {OPTIONS.map((value) => (
          <Button
            key={value}
            size="sm"
            variant={theme === value ? "default" : "outline"}
            aria-pressed={theme === value}
            onClick={() => choose(value)}
          >
            {t(`theme.${value}`)}
          </Button>
        ))}
      </CardContent>
    </Card>
  );
}
