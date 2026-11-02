"use client";

import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// Read before first paint by the inline script in app/layout.tsx.
const STORAGE_KEY = "cnp-theme";

type Theme = "system" | "light" | "dark";

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "system", label: "Navigateur" },
  { value: "light", label: "Clair" },
  { value: "dark", label: "Sombre" },
];

declare global {
  interface Window {
    __applyTheme?: () => void;
  }
}

const listeners = new Set<() => void>();

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    // Storage blocked: the browser's theme applies.
    return "system";
  }
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function ThemeCard() {
  const theme = useSyncExternalStore(subscribe, readTheme, () => "system");

  const choose = (value: Theme) => {
    try {
      if (value === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // Storage blocked: the choice is not kept.
    }
    listeners.forEach((onChange) => onChange());
    window.__applyTheme?.();
  };

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
