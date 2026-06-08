"use client";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import type { Deployment, DeploymentStatus } from "@/types";
import { useEffect, useRef, useState } from "react";

const CREATION_STEPS: { status: DeploymentStatus; label: string }[] = [
  { status: "pending", label: "Queued" },
  { status: "initializing", label: "Initializing" },
  { status: "planning", label: "Planning" },
  { status: "deploying", label: "Deploying" },
  { status: "running", label: "Running" },
];

const DELETION_STEPS: { status: DeploymentStatus; label: string }[] = [
  { status: "deleting", label: "Destroying" },
  { status: "deleted", label: "Deleted" },
];

const STATUS_PROGRESS: Record<DeploymentStatus, number> = {
  pending: 5,
  initializing: 20,
  planning: 40,
  deploying: 70,
  running: 100,
  degraded: 100,
  failed: 100,
  deleting: 50,
  deleted: 100,
};

const STATUS_COLOR: Record<DeploymentStatus, string> = {
  pending: "secondary",
  initializing: "default",
  planning: "default",
  deploying: "default",
  running: "default",
  degraded: "destructive",
  failed: "destructive",
  deleting: "secondary",
  deleted: "secondary",
};

interface Props {
  deployment: Deployment;
}

export function DeploymentStepper({ deployment }: Props) {
  const progress = STATUS_PROGRESS[deployment.status] ?? 0;
  const isFailed = deployment.status === "failed";
  const isDeleting =
    deployment.status === "deleting" || deployment.status === "deleted";
  const STEPS = isDeleting ? DELETION_STEPS : CREATION_STEPS;
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [userInteracted, setUserInteracted] = useState(false);
  const lastInteractionTimeRef = useRef(0);

  // Auto-scroll to show current step with context
  useEffect(() => {
    const currentStepIndex = STEPS.findIndex(
      (step) => step.status === deployment.status,
    );
    if (currentStepIndex === -1) return;

    // Only auto-scroll if user hasn't interacted recently (5 seconds)
    const timeSinceInteraction = Date.now() - lastInteractionTimeRef.current;
    if (userInteracted && timeSinceInteraction < 5000) return;

    const container = scrollContainerRef.current;
    if (!container) return;

    // Find the current step element
    const stepElements = container.querySelectorAll("[data-step]");
    const currentStepElement = stepElements[currentStepIndex] as HTMLElement;
    if (!currentStepElement) return;

    // Calculate scroll position to show current step with 1 previous step visible
    const containerWidth = container.offsetWidth;
    const stepLeft = currentStepElement.offsetLeft;
    const stepWidth = currentStepElement.offsetWidth;

    // Position current step at ~40% from left (not centered) to show previous steps
    const scrollPosition = stepLeft - containerWidth * 0.4 + stepWidth / 2;

    // Smooth scroll with bounds checking
    container.scrollTo({
      left: Math.max(0, scrollPosition),
      behavior: "smooth",
    });
  }, [deployment.status, userInteracted, STEPS]);

  // Reset user interaction flag after 5 seconds
  useEffect(() => {
    if (!userInteracted) return;

    const timeSinceInteraction = Date.now() - lastInteractionTimeRef.current;
    if (timeSinceInteraction < 5000) {
      const timer = setTimeout(() => {
        setUserInteracted(false);
      }, 5000 - timeSinceInteraction);
      return () => clearTimeout(timer);
    }
  }, [userInteracted]);

  // Handle user scroll/touch interaction
  const handleUserInteraction = () => {
    setUserInteracted(true);
    lastInteractionTimeRef.current = Date.now();
  };

  return (
    <div className="space-y-4">
      {/* Horizontal scrolling step indicators */}
      <div
        ref={scrollContainerRef}
        onScroll={handleUserInteraction}
        onTouchStart={handleUserInteraction}
        onMouseDown={handleUserInteraction}
        className="overflow-x-auto scrollbar-thin scrollbar-thumb-muted scrollbar-track-transparent pb-2"
        style={{ scrollbarWidth: "thin" }}
      >
        <div className="flex items-center gap-2 min-w-max px-2">
          {STEPS.map((step, i) => {
            const stepProgress = STATUS_PROGRESS[step.status];
            const currentProgress = STATUS_PROGRESS[deployment.status] ?? 0;
            const isDone = currentProgress >= stepProgress && !isFailed;
            const isActive = deployment.status === step.status;

            return (
              <div
                key={step.status}
                data-step={i}
                className="flex items-center gap-2 shrink-0"
              >
                <div
                  className={`flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold transition-all duration-300 ${
                    isFailed && isActive
                      ? "bg-destructive text-destructive-foreground"
                      : isDone
                        ? "bg-primary text-primary-foreground"
                        : isActive
                          ? "bg-primary/70 text-primary-foreground animate-pulse ring-2 ring-primary/30"
                          : "bg-muted text-muted-foreground"
                  }`}
                >
                  {isDone && !isActive ? "✓" : isDeleting ? "🗑️" : i + 1}
                </div>
                <span
                  className={`text-sm whitespace-nowrap transition-all duration-300 ${
                    isActive
                      ? "font-semibold text-foreground"
                      : "text-muted-foreground"
                  }`}
                >
                  {step.label}
                </span>
                {i < STEPS.length - 1 && (
                  <div
                    className={`h-px w-8 transition-all duration-300 ${
                      isDone ? "bg-primary" : "bg-muted"
                    }`}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Progress bar */}
      <Progress value={progress} className={isFailed ? "opacity-50" : ""} />

      {/* Status message */}
      <div className="flex items-center gap-2 flex-wrap">
        <Badge
          variant={
            STATUS_COLOR[deployment.status] as
              | "default"
              | "secondary"
              | "destructive"
              | "outline"
          }
        >
          {deployment.status.replace(/_/g, " ")}
        </Badge>
        <span className="text-sm text-muted-foreground">
          {deployment.step_message}
        </span>
      </div>
    </div>
  );
}
