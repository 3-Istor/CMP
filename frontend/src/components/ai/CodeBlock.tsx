"use client";

import { tokenizePython, type TokenKind } from "@/lib/ai/highlight";
import { cn } from "@/lib/utils";
import { useMemo } from "react";

const TOKEN_CLASS: Record<TokenKind, string> = {
  keyword: "text-violet-700 dark:text-violet-300 font-medium",
  string: "text-emerald-700 dark:text-emerald-300",
  comment: "text-muted-foreground italic",
  number: "text-amber-700 dark:text-amber-300",
  builtin: "text-sky-700 dark:text-sky-300",
  call: "text-blue-700 dark:text-blue-300",
  plain: "",
};

export function CodeBlock({ code, label, className }: { code: string; label: string; className?: string }) {
  const tokens = useMemo(() => tokenizePython(code), [code]);
  const lines = code.split("\n").length;
  return (
    <div className={cn("overflow-hidden rounded-lg border bg-muted/40", className)}>
      <pre
        aria-label={label}
        tabIndex={0}
        className="flex max-h-80 overflow-auto text-xs leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span aria-hidden className="select-none border-r bg-muted/60 px-2 py-2 text-right text-muted-foreground">
          {Array.from({ length: lines }, (_, i) => (
            <span key={i} className="block tabular-nums">{i + 1}</span>
          ))}
        </span>
        <code className="block px-3 py-2 font-mono">
          {tokens.map((token, i) =>
            token.kind === "plain" ? token.text : <span key={i} className={TOKEN_CLASS[token.kind]}>{token.text}</span>,
          )}
        </code>
      </pre>
    </div>
  );
}
