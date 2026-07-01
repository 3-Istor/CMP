"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getApiUrl } from "@/lib/api";
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
  const [logs, setLogs] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [isExpanded, setIsExpanded] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
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

    const connect = () => {
      const eventSource = new EventSource(streamUrl);
      eventSourceRef.current = eventSource;

      // The backend replays the whole log file from the start on every
      // connection. Track the line index within THIS connection so identical
      // re-sent lines never change state — only genuinely new (or changed)
      // lines update the view, avoiding pointless re-renders and scroll jumps.
      let lineIndex = 0;

      eventSource.onopen = () => {
        setIsConnected(true);
        lineIndex = 0;
      };

      eventSource.onmessage = (event) => {
        if (!event.data) return;
        const idx = lineIndex++;
        const data = event.data;
        setLogs((prev) => {
          if (idx < prev.length) {
            // Already displayed — keep the same array reference if unchanged
            if (prev[idx] === data) return prev;
            const next = prev.slice();
            next[idx] = data;
            return next;
          }
          return [...prev, data];
        });
      };

      eventSource.onerror = () => {
        setIsConnected(false);
        eventSource.close();
        // Only keep streaming while the deployment is still progressing.
        // Terminal states have a final log file, so we stop here instead of
        // letting EventSource reconnect every ~3s and re-stream the same logs.
        if (ACTIVE_STATUSES.has(statusRef.current)) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };
    };

    connect();

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
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
    setLogs(["--- Screen cleared ---"]);
  };

  // Filter logs based on search query
  const filteredLogs = logs.filter((line) =>
    line.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <Card className={`border-slate-800 bg-slate-950 text-slate-100 shadow-2xl transition-all duration-300 ${
      isMaximized ? "fixed inset-4 z-50 flex flex-col h-[calc(100vh-2rem)]" : ""
    }`}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3 border-b border-slate-800/60 bg-slate-900/40">
        <CardTitle className="text-sm font-semibold flex items-center gap-2 text-slate-200">
          <Terminal className="h-4 w-4 text-emerald-500 animate-pulse" />
          Deployment Console Logs
          {isConnected ? (
            <span className="flex h-2 w-2 rounded-full bg-emerald-500" />
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
            className="h-8 w-8 text-slate-400 hover:text-slate-200 hover:bg-slate-800"
            title="Download Logs"
          >
            <Download className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={clearLogs}
            className="h-8 w-8 text-slate-400 hover:text-slate-200 hover:bg-slate-800"
            title="Clear Console"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsMaximized(!isMaximized)}
            className="h-8 w-8 text-slate-400 hover:text-slate-200 hover:bg-slate-800"
            title={isMaximized ? "Minimize" : "Maximize"}
          >
            {isMaximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </Button>
          {!isMaximized && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsExpanded(!isExpanded)}
              className="h-8 w-8 text-slate-400 hover:text-slate-200 hover:bg-slate-800"
            >
              {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          )}
        </div>
      </CardHeader>
      
      {isExpanded && (
        <div className="flex flex-col flex-1 min-h-0">
          {/* Search bar */}
          <div className="flex items-center gap-2 p-2 border-b border-slate-900 bg-slate-900/20">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
              <Input
                type="text"
                placeholder="Filter logs..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="pl-8 h-8 text-xs bg-slate-900/60 border-slate-800 text-slate-300 placeholder:text-slate-600 focus-visible:ring-emerald-500/50"
              />
            </div>
            {filter && (
              <span className="text-[10px] text-slate-500 mr-2">
                Found {filteredLogs.length} matches
              </span>
            )}
          </div>

          {/* Console text area */}
          <CardContent 
            ref={containerRef}
            onScroll={handleScroll}
            className={`p-4 font-mono text-xs overflow-y-auto bg-black/40 flex-1 min-h-0 ${
              isMaximized ? "h-full" : "h-72 max-h-96"
            }`}
          >
            <div className="space-y-1">
              {filteredLogs.map((line, idx) => {
                // Highlight different levels of logs
                let textColor = "text-slate-300";
                if (line.includes("[ERROR]") || line.includes("❌") || line.includes("failed")) {
                  textColor = "text-rose-400 font-semibold";
                } else if (line.includes("[WARNING]") || line.includes("⚠️")) {
                  textColor = "text-amber-400";
                } else if (line.includes("---") || line.startsWith("---")) {
                  textColor = "text-emerald-400/80 font-medium";
                } else if (line.includes("✅") || line.includes("completed successfully")) {
                  textColor = "text-emerald-400 font-semibold";
                } else if (line.startsWith("Running:") || line.includes("[TF]")) {
                  textColor = "text-slate-400";
                }

                return (
                  <div key={idx} className={`whitespace-pre-wrap break-all leading-relaxed ${textColor}`}>
                    {line}
                  </div>
                );
              })}
              {filteredLogs.length === 0 && (
                <div className="text-slate-600 text-center py-8">
                  {filter ? "No logs match the filter criteria." : "Console is empty. Waiting for deployment activities..."}
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
