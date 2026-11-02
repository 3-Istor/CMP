"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getAccessToken, getApiUrl } from "@/lib/api";
import { 
  Terminal, 
  Download, 
  Trash2, 
  ChevronDown, 
  ChevronUp, 
  Search,
  Maximize2,
  Minimize2
} from "lucide-react";
import { Input } from "@/components/ui/input";

interface DeploymentLogsProps {
  deploymentId: number;
  deploymentStatus: string;
}

export function DeploymentLogs({ deploymentId, deploymentStatus }: DeploymentLogsProps) {
  const t = useTranslations("appPage.console");
  const [logs, setLogs] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [isExpanded, setIsExpanded] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Keep the latest status in a ref so the stream handlers always read the
  // current value without re-subscribing (which would reset the logs).
  const statusRef = useRef(deploymentStatus);
  useEffect(() => {
    statusRef.current = deploymentStatus;
  }, [deploymentStatus]);

  // Initialize event source for log streaming
  useEffect(() => {
    setLogs([]);
    setIsConnected(false);

    const apiUrl = getApiUrl();
    // Support relative backend URLs if API URL is relative
    const streamUrl = apiUrl.startsWith("http")
      ? `${apiUrl}/deployments/${deploymentId}/logs/stream`
      : `${window.location.origin}${apiUrl}/deployments/${deploymentId}/logs/stream`;

    const ACTIVE_STATUSES = new Set([
      "pending",
      "initializing",
      "planning",
      "deploying",
      "deleting",
    ]);

    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const scheduleReconnect = () => {
      // Only keep streaming while the deployment is still progressing.
      // Terminal states have a final log file, so we stop here instead of
      // reconnecting every ~3s and re-streaming the same logs.
      if (!stopped && ACTIVE_STATUSES.has(statusRef.current)) {
        reconnectTimer = setTimeout(connect, 3000);
      }
    };

    // fetch rather than EventSource: the API requires a bearer token, and
    // EventSource cannot send headers.
    const connect = async () => {
      const controller = new AbortController();
      streamAbortRef.current = controller;

      // The backend replays the whole log file from the start on every
      // connection. Track the line index within THIS connection so identical
      // re-sent lines never change state, only genuinely new (or changed)
      // lines update the view, avoiding pointless re-renders and scroll jumps.
      let lineIndex = 0;
      const onLine = (data: string) => {
        if (!data) return;
        const idx = lineIndex++;
        setLogs((prev) => {
          if (idx < prev.length) {
            // Already displayed, keep the same array reference if unchanged
            if (prev[idx] === data) return prev;
            const next = prev.slice();
            next[idx] = data;
            return next;
          }
          return [...prev, data];
        });
      };

      try {
        const token = await getAccessToken();
        const response = await fetch(streamUrl, {
          headers: {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        setIsConnected(true);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const event = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = event
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).replace(/^ /, ""))
              .join("\n");
            onLine(data);
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch {
        if (controller.signal.aborted) return;
      } finally {
        setIsConnected(false);
      }
      scheduleReconnect();
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
    };
  }, [deploymentId]);

  // Handle scroll events to detect if user has scrolled up
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    
    // If user is within 50px of the bottom, enable auto-scroll
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setAutoScroll(isAtBottom);
  };

  // Scroll to bottom
  useEffect(() => {
    if (autoScroll && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, autoScroll, isExpanded]);

  // Download logs as text file
  const downloadLogs = () => {
    const blob = new Blob([logs.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `deployment-${deploymentId}-logs.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  // Clear log screen locally
  const clearLogs = () => {
    setLogs([t("cleared")]);
  };

  // Filter logs based on search query
  const filteredLogs = logs.filter((line) =>
    line.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <Card className={`transition-all duration-300 ${
      isMaximized ? "fixed inset-4 z-50 flex flex-col h-[calc(100vh-2rem)]" : ""
    }`}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3 border-b">
        <CardTitle className="text-sm font-semibold flex items-center gap-2 text-foreground">
          <Terminal className="h-4 w-4 text-green-600 dark:text-green-400" />
          {t("title")}
          {isConnected ? (
            <span className="flex h-2 w-2 rounded-full bg-green-600" />
          ) : (
            <span className="flex h-2 w-2 rounded-full bg-amber-500" />
          )}
        </CardTitle>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={downloadLogs}
            disabled={logs.length === 0}
            className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-muted"
            title={t("download")}
            aria-label={t("download")}
          >
            <Download className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={clearLogs}
            className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-muted"
            title={t("clear")}
            aria-label={t("clear")}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsMaximized(!isMaximized)}
            className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-muted"
            title={isMaximized ? t("minimize") : t("maximize")}
            aria-label={isMaximized ? t("minimize") : t("maximize")}
          >
            {isMaximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </Button>
          {!isMaximized && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsExpanded(!isExpanded)}
              aria-expanded={isExpanded}
              aria-label={isExpanded ? t("collapse") : t("expand")}
              className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-muted"
            >
              {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          )}
        </div>
      </CardHeader>
      
      {isExpanded && (
        <div className="flex flex-col flex-1 min-h-0">
          {/* Search bar */}
          <div className="flex items-center gap-2 p-2 border-b">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                type="text"
                placeholder={t("filter")}
                aria-label={t("filter")}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="pl-8 h-8 text-xs "
              />
            </div>
            {filter && (
              <span className="text-[10px] text-muted-foreground mr-2">
                {t("matches", { count: filteredLogs.length })}
              </span>
            )}
          </div>

          {/* Console text area */}
          <CardContent 
            ref={containerRef}
            onScroll={handleScroll}
            className={`p-4 font-mono text-xs overflow-y-auto bg-muted/30 flex-1 min-h-0 ${
              isMaximized ? "h-full" : "h-72 max-h-96"
            }`}
          >
            <div className="space-y-1">
              {filteredLogs.map((line, idx) => {
                // Highlight different levels of logs
                let textColor = "text-foreground";
                if (line.includes("[ERROR]") || line.includes("failed")) {
                  textColor = "text-red-700 dark:text-red-400 font-semibold";
                } else if (line.includes("[WARNING]")) {
                  textColor = "text-amber-700 dark:text-amber-400";
                } else if (line.includes("---") || line.startsWith("---")) {
                  textColor = "text-green-700 dark:text-green-400 font-medium";
                } else if (line.includes("completed successfully")) {
                  textColor = "text-green-700 dark:text-green-400 font-semibold";
                } else if (line.startsWith("Running:") || line.includes("[TF]")) {
                  textColor = "text-muted-foreground";
                }

                return (
                  <div key={idx} className={`whitespace-pre-wrap break-all leading-relaxed ${textColor}`}>
                    {line}
                  </div>
                );
              })}
              {filteredLogs.length === 0 && (
                <div className="text-muted-foreground text-center py-8">
                  {filter ? t("noMatch") : t("empty")}
                </div>
              )}
              <div ref={terminalEndRef} />
            </div>
          </CardContent>
        </div>
      )}
    </Card>
  );
}
