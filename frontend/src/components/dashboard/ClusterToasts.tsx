"use client";

import { useGlobalHealth } from "@/lib/hooks";
import type { ClusterStatus } from "@/types";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

const LABELS = { aws: "EKS", gcp: "GKE" };

/** Fires a toast when a cluster finishes or fails a transition, on any page. */
export function ClusterToasts() {
  const { health } = useGlobalHealth(15000);
  const prev = useRef<Map<string, ClusterStatus>>(new Map());

  useEffect(() => {
    if (!health) return;
    for (const c of health.clusters) {
      const before = prev.current.get(c.cloud);
      if (before && before.state !== c.state) {
        const name = LABELS[c.cloud];
        const wasMoving = before.state === "starting" || before.state === "stopping";
        if (c.state === "error" && wasMoving) {
          toast.error(`${name} ${before.state === "starting" ? "start" : "stop"} failed`, {
            description: c.last_error ?? undefined,
          });
        } else if ((c.state === "up" || c.state === "down") && wasMoving) {
          toast.success(`${name} is ${c.state}`);
        }
      }
      prev.current.set(c.cloud, c);
    }
  }, [health]);

  return null;
}
