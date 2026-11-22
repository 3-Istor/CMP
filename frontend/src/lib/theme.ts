"use client";

import { useSyncExternalStore } from "react";

// Read before first paint by the inline script in app/layout.tsx.
const STORAGE_KEY = "cnp-theme";

export type Theme = "system" | "light" | "dark";

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

export function useTheme(): [Theme, (value: Theme) => void] {
  const theme = useSyncExternalStore<Theme>(subscribe, readTheme, () => "system");

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

  return [theme, choose];
}

function watchDark(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => observer.disconnect();
}

/** The theme actually applied (the "dark" class), for libraries with a theme prop. */
export function useIsDark(): boolean {
  return useSyncExternalStore(
    watchDark,
    () => document.documentElement.classList.contains("dark"),
    () => false,
  );
}
