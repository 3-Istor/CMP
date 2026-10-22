"use client";

import { useEffect, useState } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

// Read before first paint by the inline script in app/layout.tsx.
const STORAGE_KEY = "cnp-theme";

export function ThemeCard() {
  const [light, setLight] = useState(false);

  // The layout script has already set the class: mirror it.
  useEffect(() => {
    setLight(!document.documentElement.classList.contains("dark"));
  }, []);

  const toggle = (checked: boolean) => {
    setLight(checked);
    document.documentElement.classList.toggle("dark", !checked);
    try {
      localStorage.setItem(STORAGE_KEY, checked ? "light" : "dark");
    } catch {
      // Storage blocked: the choice lasts for this page only.
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Apparence</CardTitle>
        <CardDescription>Thème de l&apos;interface, gardé sur ce navigateur.</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center justify-between">
        <Label htmlFor="light-mode">Mode clair</Label>
        <Switch id="light-mode" checked={light} onCheckedChange={toggle} />
      </CardContent>
    </Card>
  );
}
