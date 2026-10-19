"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

export const MCP_URL = "https://cmp.3istor.com/mcp";

type Step = { text: string; code?: string };

type Client = {
  id: string;
  name: string;
  forWho: string;
  steps: Step[];
  note?: string;
};

export const CLIENTS: Client[] = [
  {
    id: "claude-ai",
    name: "Claude (web, desktop, mobile)",
    forWho: "Everyone. Nothing to install.",
    steps: [
      {
        text: "Open Settings, then Connectors. If your organization already added CNP, click Connect next to it and skip to the last step.",
      },
      {
        text: "Otherwise click “Add custom connector”, name it CNP and paste this URL:",
        code: MCP_URL,
      },
      {
        text: "Sign in with your company account and approve the access on the CNP page. Then just ask: “Put a page with … online”.",
      },
    ],
    note: "On Team and Enterprise plans, an Owner adds the connector once for the whole organization.",
  },
  {
    id: "claude-code",
    name: "Claude Code",
    forWho: "Developers. Brings the CNP guides with it.",
    steps: [
      {
        text: "Install the CNP plugin: it configures the connection and adds the platform guides and commands.",
        code: "/plugin marketplace add 3-Istor/cnp-agent-kit\n/plugin install cnp@cnp-agent-kit",
      },
      {
        text: "Or only the connection:",
        code: `claude mcp add --transport http cnp ${MCP_URL}`,
      },
      { text: "Run /mcp and choose cnp to sign in." },
    ],
  },
  {
    id: "chatgpt",
    name: "ChatGPT",
    forWho: "Where your plan allows custom connectors.",
    steps: [
      {
        text: "Settings, Apps & Connectors, then create a connector with this URL:",
        code: MCP_URL,
      },
      { text: "Sign in with your company account and approve the access." },
    ],
  },
  {
    id: "codex",
    name: "Codex",
    forWho: "Developers, CLI and IDE.",
    steps: [
      { text: "Add the server:", code: `codex mcp add cnp --url ${MCP_URL}` },
      { text: "Sign in:", code: "codex mcp login cnp" },
    ],
  },
  {
    id: "opencode",
    name: "OpenCode",
    forWho: "Developers.",
    steps: [
      {
        text: "In opencode.json:",
        code: `{\n  "mcp": {\n    "cnp": { "type": "remote", "url": "${MCP_URL}" }\n  }\n}`,
      },
      { text: "Sign in:", code: "opencode mcp auth cnp" },
    ],
  },
  {
    id: "cursor",
    name: "Cursor / VS Code",
    forWho: "Developers.",
    steps: [
      {
        text: "Cursor, in ~/.cursor/mcp.json:",
        code: `{\n  "mcpServers": {\n    "cnp": { "url": "${MCP_URL}" }\n  }\n}`,
      },
      {
        text: "VS Code, in .vscode/mcp.json:",
        code: `{\n  "servers": {\n    "cnp": { "type": "http", "url": "${MCP_URL}" }\n  }\n}`,
      },
      { text: "The editor opens the CNP sign-in page on first use." },
    ],
  },
];

export function CopyBlock({ code }: { code: string }) {
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
        aria-label={copied ? "Copied" : "Copy"}
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
  const [active, setActive] = useState(initial ?? CLIENTS[0].id);
  const client = CLIENTS.find((c) => c.id === active) ?? CLIENTS[0];
  return (
    <div className="grid gap-4 md:grid-cols-[13rem_1fr]">
      <div
        role="tablist"
        aria-label="Assistant"
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
            {c.name}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="min-w-0 rounded-xl border border-border bg-card p-5">
        <h3 className="font-heading text-base font-semibold">{client.name}</h3>
        <p className="mt-0.5 text-sm text-muted-foreground">{client.forWho}</p>
        <ol className="mt-4 space-y-4">
          {client.steps.map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 text-sm">
                {step.text}
                {step.code && <CopyBlock code={step.code} />}
              </div>
            </li>
          ))}
        </ol>
        {client.note && (
          <p className="mt-4 text-xs text-muted-foreground">{client.note}</p>
        )}
      </div>
    </div>
  );
}
