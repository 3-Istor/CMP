"use client";

import { useSyncExternalStore } from "react";

/**
 * localStorage-backed store of projects that were just created and are still
 * being bootstrapped on the backend. Persisting them means the "Creating…"
 * placeholder card survives a page reload while the ~30s bootstrap runs.
 *
 * Exposed via useSyncExternalStore so reads stay in sync across tabs and
 * hydrate without a server/client mismatch (server snapshot is always empty).
 */

const STORAGE_KEY = "cnp.pendingProjects";
// Safety net: drop entries this old in case a creation failed and the project
// never lands in the real list (otherwise its card would loiter forever).
const MAX_AGE_MS = 10 * 60 * 1000;

export type PendingProject = { name: string; ts: number };

const listeners = new Set<() => void>();
// Cached snapshot so useSyncExternalStore gets a stable reference between reads.
let snapshot: PendingProject[] = [];
const EMPTY: PendingProject[] = [];

function readStorage(): PendingProject[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PendingProject[];
    const now = Date.now();
    return parsed.filter((p) => p?.name && now - p.ts < MAX_AGE_MS);
  } catch {
    return [];
  }
}

function sameNames(a: PendingProject[], b: PendingProject[]): boolean {
  return a.length === b.length && a.every((p, i) => p.name === b[i]?.name);
}

function commit(next: PendingProject[]) {
  if (sameNames(next, snapshot)) return;
  snapshot = next;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void): () => void {
  // First subscriber primes the snapshot from storage.
  if (listeners.size === 0) snapshot = readStorage();
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

/** Read the persisted pending list (stable reference until it changes). */
export function usePendingProjects(): PendingProject[] {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => EMPTY,
  );
}

/** Mark a freshly created project as pending. */
export function addPendingProject(name: string) {
  if (snapshot.some((p) => p.name === name)) return;
  commit([...snapshot, { name, ts: Date.now() }]);
}

/** Remove any pending entries whose project now exists in the real list. */
export function prunePendingProjects(existing: { name: string }[]) {
  const next = snapshot.filter(
    (p) => !existing.some((e) => e.name === p.name),
  );
  commit(next);
}
