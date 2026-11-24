"use client";

import { useCallback, useState, useSyncExternalStore } from "react";

export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
export const DEFAULT_PAGE_SIZE = 20;

const PREFIX = "cmp.pageSize.";

function readStored(storageKey: string, options: number[]): number | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + storageKey);
    const size = raw === null ? NaN : Number(raw);
    return options.includes(size) ? size : null;
  } catch {
    return null;
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/**
 * A page size remembered in this browser under `storageKey`. Storage can be
 * missing (private window, blocked site data): the size then lasts the visit.
 */
export function usePageSize(
  storageKey: string | undefined,
  defaultSize: number = DEFAULT_PAGE_SIZE,
  options: number[] = PAGE_SIZE_OPTIONS,
): [number, (size: number) => void] {
  const [chosen, setChosen] = useState<number | null>(null);
  // The server render has no storage: it renders the default, then the stored size.
  const stored = useSyncExternalStore(
    subscribe,
    () => (storageKey ? readStored(storageKey, options) : null),
    () => null,
  );

  const update = useCallback(
    (next: number) => {
      setChosen(next);
      if (!storageKey) return;
      try {
        window.localStorage.setItem(PREFIX + storageKey, String(next));
      } catch {
        // The size still applies for this visit.
      }
    },
    [storageKey],
  );

  return [chosen ?? stored ?? defaultSize, update];
}
