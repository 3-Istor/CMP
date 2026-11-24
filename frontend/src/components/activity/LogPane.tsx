"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LogLine } from "@/types";
import { Copy, Link2, ListTree, Waypoints } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useRef } from "react";
import { toast } from "sonner";
import {
  highlightParts,
  parseFields,
  severityOf,
  TOKEN_CLASS,
  tokenize,
} from "./logHighlight";

/** A line from any source; the observability store also gives level and trace. */
export type Row = LogLine & {
  level?: string | null;
  trace_id?: string | null;
  /** A stable key when time, pod and container do not tell lines apart (the deployment console). */
  id?: string;
};

const LEVELS: Record<string, "error" | "warn"> = {
  error: "error",
  err: "error",
  fatal: "error",
  critical: "error",
  panic: "error",
  warn: "warn",
  warning: "warn",
};

export function severity(row: Row): "error" | "warn" | null {
  return row.level ? (LEVELS[row.level.toLowerCase()] ?? null) : severityOf(row.line);
}

// One colour per process, as docker compose does; readable on both themes.
const PALETTE = [
  "text-sky-600 dark:text-sky-400",
  "text-emerald-600 dark:text-emerald-400",
  "text-violet-600 dark:text-violet-400",
  "text-amber-600 dark:text-amber-400",
  "text-pink-600 dark:text-pink-400",
  "text-teal-600 dark:text-teal-400",
  "text-orange-600 dark:text-orange-400",
  "text-indigo-600 dark:text-indigo-400",
  "text-lime-600 dark:text-lime-400",
  "text-rose-600 dark:text-rose-400",
];

/** The process a line comes from: the pod without its generated suffixes. */
export function streamOf(l: LogLine, project: string): string {
  const base = l.pod
    .replace(/-[a-z0-9]{8,10}-[a-z0-9]{5}$/, "")
    .replace(/-[a-z0-9]{5}$/, "")
    .replace(`${project}-`, "")
    .replace(/-cnp-generic-app$/, "");
  return l.container &&
    !base.endsWith(l.container) &&
    !l.pod.includes(l.container)
    ? `${base}/${l.container}`
    : base;
}

export function colourOf(stream: string): string {
  let hash = 0;
  for (const ch of stream) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

export function rowKey(l: Row): string {
  return l.id ?? `${l.time}|${l.pod}|${l.container}|${l.line}`;
}

function clock(iso: string, locale: string): string {
  return new Date(iso).toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function Marked({ text, pattern }: { text: string; pattern: RegExp | null }) {
  return (
    <>
      {highlightParts(text, pattern).map((part, i) =>
        part.hit ? (
          <mark
            key={i}
            className="rounded bg-yellow-300/70 px-0.5 text-inherit dark:bg-yellow-500/40"
          >
            {part.text}
          </mark>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

/** A line with its tokens coloured and the search matches marked. */
function Colourised({ text, pattern }: { text: string; pattern: RegExp | null }) {
  return (
    <>
      {tokenize(text).map((t, i) =>
        t.kind ? (
          <span key={i} className={TOKEN_CLASS[t.kind]}>
            <Marked text={t.text} pattern={pattern} />
          </span>
        ) : (
          <Marked key={i} text={t.text} pattern={pattern} />
        ),
      )}
    </>
  );
}

async function copy(text: string, done: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(done);
  } catch {
    toast.error(text);
  }
}

function LineDetails({
  row,
  onShowContext,
  copyLink,
}: {
  row: Row;
  onShowContext?: (row: Row) => void;
  copyLink?: () => string;
}) {
  const t = useTranslations("activity.logs.details");
  const fields = useMemo(() => parseFields(row.line), [row.line]);
  const meta: [string, string][] = [
    ...(row.id ? [] : ([["time", row.time]] as [string, string][])),
    ...(row.pod ? ([["pod", row.pod]] as [string, string][]) : []),
    ...(row.container ? ([["container", row.container]] as [string, string][]) : []),
    ...(row.level ? ([["level", row.level]] as [string, string][]) : []),
  ];
  return (
    <div
      className="mx-3 my-1 space-y-2 rounded-lg border bg-background p-3 font-sans text-xs"
      onClick={(e) => e.stopPropagation()}
    >
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 font-mono">
        {[...meta, ...(fields ?? [])].map(([k, v], i) => (
          <div key={`${k}-${i}`} className="contents">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="break-all whitespace-pre-wrap">{v}</dd>
          </div>
        ))}
      </dl>
      {!fields && <p className="text-muted-foreground">{t("noFields")}</p>}
      <div className="flex flex-wrap gap-2">
        {onShowContext && (
          <Button variant="outline" size="xs" onClick={() => onShowContext(row)}>
            <ListTree /> {t("context")}
          </Button>
        )}
        <Button variant="outline" size="xs" onClick={() => copy(row.line, t("copied"))}>
          <Copy /> {t("copyLine")}
        </Button>
        {copyLink && (
          <Button
            variant="outline"
            size="xs"
            onClick={() => copy(copyLink(), t("linkCopied"))}
          >
            <Link2 /> {t("copyLink")}
          </Button>
        )}
      </div>
    </div>
  );
}

export interface LogPaneProps {
  rows: Row[] | null;
  /** Colours lines per process; without it the process column is hidden. */
  project?: string;
  /** Links lines carrying a trace id to that trace. */
  appId?: number;
  pattern: RegExp | null;
  wrap: boolean;
  showTime: boolean;
  className: string;
  emptyText: string;
  /** Lines open their details on click. */
  expandable?: boolean;
  openKey?: string | null;
  onToggle?: (key: string) => void;
  /** The line the context view is centred on. */
  anchor?: string | null;
  onShowContext?: (row: Row) => void;
  copyLink?: () => string;
  /** A ghost of this many lines while the first answer is awaited. */
  skeletonLines?: number;
}

export function LogPane({
  rows,
  project,
  appId,
  pattern,
  wrap,
  showTime,
  className,
  emptyText,
  expandable = false,
  openKey,
  onToggle,
  anchor,
  onShowContext,
  copyLink,
  skeletonLines = 12,
}: LogPaneProps) {
  const t = useTranslations("activity");
  const locale = useLocale();
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  // Follow the end like a terminal, unless the reader scrolled up or is reading a context.
  useEffect(() => {
    const el = scroller.current;
    if (el && atBottom.current && !anchor && !openKey) el.scrollTop = el.scrollHeight;
  }, [rows, anchor, openKey]);

  const width = useMemo(() => {
    if (!project) return 0;
    const names = (rows ?? []).map((l) => streamOf(l, project).length);
    return Math.min(28, Math.max(8, ...names));
  }, [rows, project]);

  return (
    <div
      ref={scroller}
      onScroll={(e) => {
        const el = e.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
      className={cn(
        "overflow-auto rounded-xl border bg-zinc-50 py-1 font-mono text-xs leading-5 dark:bg-zinc-950",
        className,
      )}
      aria-busy={rows === null}
    >
      {rows === null ? (
        Array.from({ length: skeletonLines }, (_, i) => (
          <div key={i} className="flex gap-3 px-3 py-0.5" aria-hidden>
            <div className="h-3.5 w-16 animate-pulse rounded bg-muted" />
            <div
              className="h-3.5 animate-pulse rounded bg-muted"
              style={{ width: `${35 + ((i * 37) % 55)}%` }}
            />
          </div>
        ))
      ) : rows.length === 0 ? (
        <p className="p-4 text-muted-foreground">{emptyText}</p>
      ) : (
        rows.map((l, i) => {
          const k = rowKey(l);
          const s = project ? streamOf(l, project) : "";
          // A new block starts when the process changes: a hairline and a
          // little air, and the name only at the top of the block.
          const first = !project || i === 0 || streamOf(rows[i - 1], project) !== s;
          const level = severity(l);
          const open = expandable && openKey === k;
          return (
            <div key={`${k}#${i}`}>
              <div
                onClick={
                  expandable && onToggle
                    ? () => {
                        // Selecting text to copy it must not open the line.
                        if (window.getSelection()?.toString()) return;
                        onToggle(k);
                      }
                    : undefined
                }
                className={cn(
                  "group flex gap-3 border-l-2 border-transparent px-3 hover:bg-muted/60",
                  project && first && i > 0 && "mt-1 border-t border-t-border/70 pt-1",
                  level === "error" && "border-l-red-500 bg-red-500/5",
                  level === "warn" && "border-l-amber-500",
                  (anchor === k || open) && "bg-primary/10",
                  expandable && "cursor-pointer",
                )}
              >
                {showTime && !l.id && (
                  <span className="shrink-0 text-muted-foreground">
                    {clock(l.time, locale)}
                  </span>
                )}
                {project && (
                  <>
                    <span
                      className={cn(
                        "shrink-0 truncate text-right font-semibold",
                        colourOf(s),
                        !first && "opacity-35",
                      )}
                      style={{ width: `${width}ch` }}
                      title={`${l.pod} / ${l.container}`}
                    >
                      {s}
                    </span>
                    <span className="shrink-0 text-muted-foreground">|</span>
                  </>
                )}
                <span
                  className={cn(
                    "min-w-0 flex-1",
                    wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre",
                  )}
                >
                  <Colourised text={l.line} pattern={pattern} />
                </span>
                {l.trace_id && appId != null && project && (
                  <Link
                    href={`/projects/${encodeURIComponent(project)}/apps/${appId}/traces?trace=${encodeURIComponent(l.trace_id)}`}
                    title={t("logs.openTrace")}
                    aria-label={t("logs.openTrace")}
                    onClick={(e) => e.stopPropagation()}
                    className="flex h-5 shrink-0 items-center self-start rounded px-1 text-primary hover:bg-muted"
                  >
                    <Waypoints className="h-3.5 w-3.5" aria-hidden />
                  </Link>
                )}
              </div>
              {open && (
                <LineDetails row={l} onShowContext={onShowContext} copyLink={copyLink} />
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
