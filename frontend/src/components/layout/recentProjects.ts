const STORAGE_KEY = "cmp.recentProjects";
const MAX_RECENT = 20;

export function readRecentProjects(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter((n): n is string => typeof n === "string")
      : [];
  } catch {
    return [];
  }
}

export function recordRecentProject(projectName: string): void {
  try {
    const next = [
      projectName,
      ...readRecentProjects().filter((n) => n !== projectName),
    ].slice(0, MAX_RECENT);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    return;
  }
}
