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

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "system", label: "Navigateur" },
  { value: "light", label: "Clair" },
  { value: "dark", label: "Sombre" },
];

export function ThemeCard() {
  const [theme, choose] = useTheme();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Apparence</CardTitle>
        <CardDescription>
          Par défaut, le thème suit celui du navigateur. Choix gardé sur ce navigateur.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex gap-2">
        {OPTIONS.map(({ value, label }) => (
          <Button
            key={value}
            size="sm"
            variant={theme === value ? "default" : "outline"}
            aria-pressed={theme === value}
            onClick={() => choose(value)}
          >
            {label}
          </Button>
        ))}
      </CardContent>
    </Card>
  );
}
