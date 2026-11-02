"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { getFavorites, updateFavorites, type Favorites } from "./api";

export const MAX_FAVORITES = 50;

interface State {
  favorites: Favorites;
  loaded: boolean;
}

let state: State = { favorites: { projects: [], apps: [] }, loaded: false };
let loading: Promise<void> | null = null;
let saveQueue: Promise<unknown> = Promise.resolve();
const listeners = new Set<() => void>();

function setState(next: State) {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function load(): Promise<void> {
  if (state.loaded) return Promise.resolve();
  loading ??= getFavorites()
    .then((favorites) => setState({ favorites, loaded: true }))
    .catch(() => undefined)
    .finally(() => {
      loading = null;
    });
  return loading;
}

export type ToggleResult = "ok" | "limit" | "failed";

async function toggle(
  key: keyof Favorites,
  id: string | number,
): Promise<ToggleResult> {
  await load();
  const previous = state.favorites;
  const current = previous[key] as (string | number)[];
  const next = current.includes(id)
    ? current.filter((v) => v !== id)
    : [...current, id];
  if (next.length > MAX_FAVORITES) return "limit";

  const optimistic = { ...previous, [key]: next } as Favorites;
  setState({ favorites: optimistic, loaded: true });

  const saved = saveQueue.then(() => updateFavorites(optimistic));
  saveQueue = saved.catch(() => undefined);
  try {
    await saved;
    return "ok";
  } catch {
    if (state.favorites === optimistic) {
      setState({ favorites: previous, loaded: true });
    }
    return "failed";
  }
}

export function useFavorites() {
  const snapshot = useSyncExternalStore(
    subscribe,
    () => state,
    () => state,
  );

  useEffect(() => {
    void load();
  }, []);

  const isFavoriteProject = useCallback(
    (name: string) => snapshot.favorites.projects.includes(name),
    [snapshot],
  );
  const isFavoriteApp = useCallback(
    (id: number) => snapshot.favorites.apps.includes(id),
    [snapshot],
  );

  return {
    projects: snapshot.favorites.projects,
    apps: snapshot.favorites.apps,
    loaded: snapshot.loaded,
    isFavoriteProject,
    isFavoriteApp,
    toggleProject: (name: string) => toggle("projects", name),
    toggleApp: (id: number) => toggle("apps", id),
  };
}
