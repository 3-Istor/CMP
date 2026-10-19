"use client";

import { Check, Copy, ExternalLink, Terminal } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import { MCP_URL } from "./ClientInstructions";

// Documented install links: Cursor (cursor.com/docs/mcp/install-links) and
// VS Code (vscode:mcp/install). claude.ai has none for custom connectors, so
// its button copies the URL and opens the page where it is pasted.
const CURSOR_LINK = `cursor://anysphere.cursor-deeplink/mcp/install?name=cnp&config=${
  typeof btoa === "undefined" ? "" : btoa(JSON.stringify({ url: MCP_URL }))
}`;
const VSCODE_LINK = `vscode:mcp/install?${encodeURIComponent(
  JSON.stringify({ name: "cnp", type: "http", url: MCP_URL }),
)}`;
const CLAUDE_CONNECTORS = "https://claude.ai/customize/connectors";
const CLAUDE_CODE_COMMAND = `claude mcp add --transport http cnp ${MCP_URL}`;

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

const button =
  "inline-flex items-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-primary";

export function InstallButtons({ className }: { className?: string }) {
  const [claudeHint, setClaudeHint] = useState(false);
  const [copiedCommand, setCopiedCommand] = useState(false);

  const addToClaude = async () => {
    await copy(MCP_URL);
    window.open(CLAUDE_CONNECTORS, "_blank", "noopener");
    setClaudeHint(true);
  };

  const copyCommand = async () => {
    if (await copy(CLAUDE_CODE_COMMAND)) {
      setCopiedCommand(true);
      setTimeout(() => setCopiedCommand(false), 2000);
    }
  };

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={addToClaude}
          className={cn(
            button,
            "border-primary bg-primary text-primary-foreground hover:bg-primary/85",
          )}
        >
          Add to Claude
          <ExternalLink className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={copyCommand}
          className={cn(button, "border-border bg-card hover:bg-muted")}
          title={CLAUDE_CODE_COMMAND}
        >
          {copiedCommand ? (
            <Check className="h-4 w-4 text-primary" />
          ) : (
            <Terminal className="h-4 w-4" />
          )}
          {copiedCommand ? "Command copied" : "Claude Code"}
        </button>
        <a
          href={CURSOR_LINK}
          className={cn(button, "border-border bg-card hover:bg-muted")}
        >
          Add to Cursor
        </a>
        <a
          href={VSCODE_LINK}
          className={cn(button, "border-border bg-card hover:bg-muted")}
        >
          Add to VS Code
        </a>
      </div>
      {claudeHint && (
        <div
          role="status"
          className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm"
        >
          <p className="font-medium">
            The CNP address is copied. In the Claude tab that just opened:
          </p>
          <ol className="mt-1.5 list-decimal space-y-0.5 pl-5 text-muted-foreground">
            <li>
              If CNP is already listed (your organization added it), click{" "}
              <strong className="text-foreground">Connect</strong>.
            </li>
            <li>
              Otherwise click <strong className="text-foreground">+</strong>,
              then <strong className="text-foreground">Add custom connector</strong>,
              name it CNP and paste the address.
            </li>
            <li>Sign in with your company account and allow access.</li>
          </ol>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <code className="rounded bg-muted px-1.5 py-0.5">{MCP_URL}</code>
            <button
              type="button"
              onClick={() => copy(MCP_URL)}
              className="inline-flex items-center gap-1 hover:text-foreground"
            >
              <Copy className="h-3 w-3" /> copy again
            </button>
            <span>· Team or Enterprise owner:</span>
            <a
              href="https://claude.ai/admin-settings/connectors"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              add it for everyone
            </a>
          </p>
        </div>
      )}
    </div>
  );
}
