/**
 * Light syntax colouring of log lines, after what terminal colourisers and the
 * VS Code Log File Highlighter mark: timestamps, levels, URLs, IPs, UUIDs,
 * HTTP methods, quoted strings, keys of key=value pairs, durations, numbers.
 */

const RULES: { kind: string; pattern: string }[] = [
  {
    kind: "time",
    pattern: String.raw`\b\d{4}[-/]\d{2}[-/]\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b`,
  },
  { kind: "url", pattern: String.raw`\bhttps?://[^\s"'<>]+` },
  {
    kind: "uuid",
    pattern: String.raw`\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b`,
  },
  { kind: "ip", pattern: String.raw`\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b` },
  // Before the levels, so success=true is a key, not a level.
  { kind: "key", pattern: String.raw`\b[A-Za-z_][\w.-]*(?==)` },
  {
    kind: "error",
    pattern: String.raw`\b(?:ERROR|ERR|FATAL|CRITICAL|PANIC|FAIL(?:ED|URE)?|error|fatal|panic|failed|Exception|Traceback)\b`,
  },
  { kind: "warn", pattern: String.raw`\b(?:WARN(?:ING)?|warn(?:ing)?)\b` },
  {
    kind: "info",
    pattern: String.raw`\b(?:INFO|NOTICE|info|notice|SUCCESS|success|OK)\b`,
  },
  { kind: "debug", pattern: String.raw`\b(?:DEBUG|TRACE|debug|trace)\b` },
  {
    kind: "method",
    pattern: String.raw`\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b`,
  },
  { kind: "string", pattern: String.raw`"(?:[^"\\]|\\.)*"` },
  { kind: "constant", pattern: String.raw`\b(?:true|false|null|nil|None)\b` },
  {
    kind: "number",
    pattern: String.raw`\b\d+(?:\.\d+)?(?:ms|µs|us|ns|s|m|h|Mi|Gi|Ki|MB|GB|KB|%)?\b`,
  },
];

const PATTERN = new RegExp(RULES.map((r) => `(${r.pattern})`).join("|"), "g");

export const TOKEN_CLASS: Record<string, string> = {
  time: "text-sky-700 dark:text-sky-400",
  url: "text-blue-700 underline decoration-dotted dark:text-blue-400",
  uuid: "text-slate-500 dark:text-slate-400",
  ip: "text-fuchsia-700 dark:text-fuchsia-400",
  error: "font-semibold text-red-700 dark:text-red-400",
  warn: "font-semibold text-amber-700 dark:text-amber-400",
  info: "text-green-700 dark:text-green-400",
  debug: "text-slate-500 dark:text-slate-400",
  method: "font-semibold text-teal-700 dark:text-teal-400",
  string: "text-emerald-700 dark:text-emerald-400",
  key: "text-slate-500 dark:text-slate-400",
  constant: "text-violet-700 dark:text-violet-400",
  number: "text-orange-700 dark:text-orange-400",
};

export interface Token {
  text: string;
  kind: string | null;
}

export function tokenize(line: string): Token[] {
  const tokens: Token[] = [];
  let last = 0;
  for (const match of line.matchAll(PATTERN)) {
    const index = match.index ?? 0;
    if (index > last)
      tokens.push({ text: line.slice(last, index), kind: null });
    const group = match.slice(1).findIndex((g) => g !== undefined);
    tokens.push({ text: match[0], kind: RULES[group]?.kind ?? null });
    last = index + match[0].length;
  }
  if (last < line.length) tokens.push({ text: line.slice(last), kind: null });
  return tokens;
}

export function severityOf(line: string): "error" | "warn" | null {
  if (/\b(error|err|fatal|panic|exception|traceback)\b/i.test(line))
    return "error";
  if (/\b(warn|warning)\b/i.test(line)) return "warn";
  return null;
}
