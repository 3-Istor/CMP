const STORAGE_KEY = "cmp.recentApps";
const MAX_RECENT = 20;

export function readRecentApps(): number[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter((n): n is number => Number.isInteger(n))
      : [];
  } catch {
    return [];
  }
}

export function recordRecentApp(appId: number): void {
  try {
    const next = [
      appId,
      ...readRecentApps().filter((n) => n !== appId),
    ].slice(0, MAX_RECENT);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    return;
  }
}
