"use client";

import { Check, Copy } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { cn } from "@/lib/utils";

export const MCP_URL = "https://cmp.3istor.com/mcp";

type Step = { text: string; code?: string };

type Client = {
  id: string;
  key: string;
  steps: Step[];
  hasNote?: boolean;
};

export const CLIENTS: Client[] = [
  {
    id: "claude-ai",
    key: "claudeAi",
    steps: [
      { text: "step1" },
      { text: "step2", code: MCP_URL },
      { text: "step3" },
    ],
    hasNote: true,
  },
  {
    id: "claude-code",
    key: "claudeCode",
    steps: [
      {
        text: "step1",
        code: "/plugin marketplace add 3-Istor/cnp-agent-kit\n/plugin install cnp@cnp-agent-kit",
      },
      {
        text: "step2",
        code: `claude mcp add --transport http cnp ${MCP_URL}`,
      },
      { text: "step3" },
    ],
  },
  {
    id: "chatgpt",
    key: "chatgpt",
    steps: [
      { text: "step1", code: MCP_URL },
      { text: "step2" },
    ],
  },
  {
    id: "codex",
    key: "codex",
    steps: [
      { text: "step1", code: `codex mcp add cnp --url ${MCP_URL}` },
      { text: "step2", code: "codex mcp login cnp" },
    ],
  },
  {
    id: "opencode",
    key: "opencode",
    steps: [
      {
        text: "step1",
        code: `{\n  "mcp": {\n    "cnp": { "type": "remote", "url": "${MCP_URL}" }\n  }\n}`,
      },
      { text: "step2", code: "opencode mcp auth cnp" },
    ],
  },
  {
    id: "cursor",
    key: "cursor",
    steps: [
      {
        text: "step1",
        code: `{\n  "mcpServers": {\n    "cnp": { "url": "${MCP_URL}" }\n  }\n}`,
      },
      {
        text: "step2",
        code: `{\n  "servers": {\n    "cnp": { "type": "http", "url": "${MCP_URL}" }\n  }\n}`,
      },
      { text: "step3" },
    ],
  },
];

export function CopyBlock({ code }: { code: string }) {
  const t = useTranslations("agents.copyBlock");
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard refused: the text stays selectable.
    }
  };
  return (
    <div className="relative mt-2">
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/50 py-2.5 pr-11 pl-3 font-mono text-xs leading-relaxed">
        <code>{code}</code>
      </pre>
      <button
        type="button"
        onClick={copy}
        className="absolute top-1.5 right-1.5 rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={copied ? t("copied") : t("copy")}
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-primary" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </button>
    </div>
  );
}

export function ClientInstructions({ initial }: { initial?: string }) {
  const t = useTranslations("agents");
  const tCopy = useTranslations("agents.copyBlock");
  const [active, setActive] = useState(initial ?? CLIENTS[0].id);
  const client = CLIENTS.find((c) => c.id === active) ?? CLIENTS[0];
  const base = `clients.${client.key}`;
  return (
    <div className="grid gap-4 md:grid-cols-[13rem_1fr]">
      <div
        role="tablist"
        aria-label={tCopy("assistant")}
        className="flex gap-1 overflow-x-auto md:flex-col"
      >
        {CLIENTS.map((c) => (
          <button
            key={c.id}
            role="tab"
            type="button"
            aria-selected={c.id === active}
            onClick={() => setActive(c.id)}
            className={cn(
              "shrink-0 rounded-lg px-3 py-2 text-left text-sm transition-colors",
              c.id === active
                ? "bg-primary/10 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {t(`clients.${c.key}.name`)}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="min-w-0 rounded-xl border border-border bg-card p-5">
        <h3 className="font-heading text-base font-semibold">
          {t(`${base}.name`)}
        </h3>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t(`${base}.forWho`)}
        </p>
        <ol className="mt-4 space-y-4">
          {client.steps.map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 text-sm">
                {t(`${base}.${step.text}`)}
                {step.code && <CopyBlock code={step.code} />}
              </div>
            </li>
          ))}
        </ol>
        {client.hasNote && (
          <p className="mt-4 text-xs text-muted-foreground">
            {t(`${base}.note`)}
          </p>
        )}
      </div>
    </div>
  );
}
