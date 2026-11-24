"use client";

import { getAccessToken, getApiUrl } from "@/lib/api";
import { useEffect, useRef, useState } from "react";

const ACTIVE_STATUSES = new Set([
  "pending",
  "initializing",
  "planning",
  "deploying",
  "deleting",
]);

/** The deployment's console, streamed over server-sent events. */
export function useDeploymentStream(deploymentId: number, deploymentStatus: string) {
  const [lines, setLines] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);

  // The handlers read the latest status without re-subscribing, which would reset the lines.
  const statusRef = useRef(deploymentStatus);
  useEffect(() => {
    statusRef.current = deploymentStatus;
  }, [deploymentStatus]);

  useEffect(() => {
    setLines([]);
    setConnected(false);

    const apiUrl = getApiUrl();
    const streamUrl = apiUrl.startsWith("http")
      ? `${apiUrl}/deployments/${deploymentId}/logs/stream`
      : `${window.location.origin}${apiUrl}/deployments/${deploymentId}/logs/stream`;

    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let controller: AbortController | null = null;

    // Terminal states have a final log file: reconnecting would only replay it.
    const scheduleReconnect = () => {
      if (!stopped && ACTIVE_STATUSES.has(statusRef.current)) {
        reconnectTimer = setTimeout(connect, 3000);
      }
    };

    // fetch rather than EventSource: the API requires a bearer token, and
    // EventSource cannot send headers.
    const connect = async () => {
      controller = new AbortController();
      const signal = controller.signal;

      // The backend replays the whole file on every connection: lines are
      // indexed within this connection so a replayed line changes nothing.
      let lineIndex = 0;
      const onLine = (data: string) => {
        if (!data) return;
        const idx = lineIndex++;
        setLines((prev) => {
          if (idx < prev.length) {
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
          signal,
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        setConnected(true);

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
            onLine(
              event
                .split("\n")
                .filter((line) => line.startsWith("data:"))
                .map((line) => line.slice(5).replace(/^ /, ""))
                .join("\n"),
            );
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch {
        if (signal.aborted) return;
      } finally {
        setConnected(false);
      }
      scheduleReconnect();
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      controller?.abort();
    };
  }, [deploymentId]);

  return { lines, connected };
}
