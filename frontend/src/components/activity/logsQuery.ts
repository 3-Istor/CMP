/** What a logs link carries in its query string, so a copied link opens the same view. */
export interface LogsQuery {
  period?: string;
  level?: "error" | "warn" | "info" | "debug";
  q?: string;
  regex?: boolean;
  pod?: string;
  container?: string;
}

const LEVELS = new Set(["error", "warn", "info", "debug"]);
const PERIODS = new Set(["15m", "1h", "6h", "24h", "7d"]);

export function logsQueryFromParams(params: URLSearchParams): LogsQuery {
  const level = params.get("level");
  const period = params.get("period");
  return {
    period: period && PERIODS.has(period) ? period : undefined,
    level: level && LEVELS.has(level) ? (level as LogsQuery["level"]) : undefined,
    q: params.get("q") || undefined,
    regex: params.get("regex") === "1" || undefined,
    pod: params.get("pod") || undefined,
    container: params.get("container") || undefined,
  };
}

export function logsQueryToParams(query: LogsQuery, into = new URLSearchParams()) {
  if (query.period) into.set("period", query.period);
  if (query.level) into.set("level", query.level);
  if (query.q) into.set("q", query.q);
  if (query.regex && query.q) into.set("regex", "1");
  if (query.pod) into.set("pod", query.pod);
  if (query.container) into.set("container", query.container);
  return into;
}
