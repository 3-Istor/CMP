"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { streamClusterLogs } from "@/lib/api";
import type { ClusterCloud } from "@/types";
import { useEffect, useRef, useState } from "react";

export function ClusterLogsDialog({
  cloud,
  label,
  open,
  onOpenChange,
}: {
  cloud: ClusterCloud;
  label: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [lines, setLines] = useState<string[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setLines([]);
    const controller = new AbortController();
    // The stream replays the log from the start on each connection.
    streamClusterLogs(
      cloud,
      (l) => setLines((prev) => [...prev, l]),
      controller.signal,
    ).catch(() => {});
    return () => controller.abort();
  }, [open, cloud]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [lines]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{label} logs</DialogTitle>
        </DialogHeader>
        <div className="h-96 overflow-auto rounded bg-muted/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
          {lines.length === 0 ? (
            <span className="text-muted-foreground">No logs yet.</span>
          ) : (
            lines.map((l, i) => <div key={i}>{l}</div>)
          )}
          <div ref={endRef} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
