"use client";

import { useSyncExternalStore } from "react";

/**
 * localStorage-backed store of projects whose delete was just confirmed and
 * are still being torn down on the backend. Mirrors pendingProjects.ts, but
 * inverted: a project here is pruned once it *disappears* from the real
 * list (deleteProject only synchronously drops the DB row and Git record —
 * Keycloak group removal happens in the background Terraform destroy, which
 * is what the project list is actually derived from, so the project stays
 * listed for a while after the API call returns).
 */

const STORAGE_KEY = "cnp.pendingDeletions";
// Safety net: stop treating a project as "deleting" after this long in case
// the background teardown failed and it never leaves the list, so its card
// doesn't stay disabled forever.
const MAX_AGE_MS = 10 * 60 * 1000;

export type PendingDeletion = { name: string; ts: number };

const listeners = new Set<() => void>();
let snapshot: PendingDeletion[] = [];
const EMPTY: PendingDeletion[] = [];

function readStorage(): PendingDeletion[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PendingDeletion[];
    const now = Date.now();
    return parsed.filter((p) => p?.name && now - p.ts < MAX_AGE_MS);
  } catch {
    return [];
  }
}

function sameNames(a: PendingDeletion[], b: PendingDeletion[]): boolean {
  return a.length === b.length && a.every((p, i) => p.name === b[i]?.name);
}

function commit(next: PendingDeletion[]) {
  if (sameNames(next, snapshot)) return;
  snapshot = next;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void): () => void {
  if (listeners.size === 0) snapshot = readStorage();
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

/** Read the persisted pending-deletion list (stable reference until it changes). */
export function usePendingDeletions(): PendingDeletion[] {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => EMPTY,
  );
}

/** Mark a project as being deleted. */
export function addPendingDeletion(name: string) {
  if (snapshot.some((p) => p.name === name)) return;
  commit([...snapshot, { name, ts: Date.now() }]);
}

/** Drop pending-deletion entries for projects no longer in the real list. */
export function prunePendingDeletions(existing: { name: string }[]) {
  const next = snapshot.filter((p) =>
    existing.some((e) => e.name === p.name),
  );
  commit(next);
}
