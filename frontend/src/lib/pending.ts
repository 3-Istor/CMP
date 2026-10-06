"use client";

import { useSyncExternalStore } from "react";

/**
 * localStorage-backed lists of projects whose create or delete is still
 * running on the backend, so their placeholder/disabled card survives a page
 * reload. Exposed via useSyncExternalStore so reads stay in sync across tabs
 * and hydrate without a server/client mismatch (server snapshot is empty).
 *
 * - pendingProjects: just created, still bootstrapping (~30s). Pruned once
 *   the project *appears* in the real list.
 * - pendingDeletions: delete confirmed, teardown still running. Pruned once
 *   the project *disappears* — deleteProject only drops the DB row and Git
 *   record synchronously; the Keycloak group the list is derived from goes
 *   away later in the background Terraform destroy.
 */

// Safety net: drop entries this old in case the backend job failed and the
// project never reaches its final state (otherwise its card would loiter).
const MAX_AGE_MS = 10 * 60 * 1000;

type Pending = { name: string; ts: number };
const EMPTY: Pending[] = [];

function createPendingStore(storageKey: string, pruneWhenListed: boolean) {
  const listeners = new Set<() => void>();
  // Cached snapshot so useSyncExternalStore gets a stable reference.
  let snapshot: Pending[] = [];

  function readStorage(): Pending[] {
    if (typeof window === "undefined") return [];
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return [];
      const now = Date.now();
      return (JSON.parse(raw) as Pending[]).filter(
        (p) => p?.name && now - p.ts < MAX_AGE_MS,
      );
    } catch {
      return [];
    }
  }

  function commit(next: Pending[]) {
    const same =
      next.length === snapshot.length &&
      next.every((p, i) => p.name === snapshot[i]?.name);
    if (same) return;
    snapshot = next;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(storageKey, JSON.stringify(next));
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

  function usePending(): Pending[] {
    return useSyncExternalStore(subscribe, () => snapshot, () => EMPTY);
  }

  return {
    usePending,
    add(name: string) {
      if (snapshot.some((p) => p.name === name)) return;
      commit([...snapshot, { name, ts: Date.now() }]);
    },
    prune(existing: { name: string }[]) {
      commit(
        snapshot.filter(
          (p) => existing.some((e) => e.name === p.name) !== pruneWhenListed,
        ),
      );
    },
  };
}

const projects = createPendingStore("cnp.pendingProjects", true);
const deletions = createPendingStore("cnp.pendingDeletions", false);

export const usePendingProjects = projects.usePending;
export const addPendingProject = projects.add;
export const prunePendingProjects = projects.prune;

export const usePendingDeletions = deletions.usePending;
export const addPendingDeletion = deletions.add;
export const prunePendingDeletions = deletions.prune;
