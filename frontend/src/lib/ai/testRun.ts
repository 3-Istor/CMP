import type { AgentTestEvent, RefusalReason } from "@/types/ai";

export interface Artifact {
  name: string;
  mime: string;
  url: string;
}

export type RunStep =
  | {
      kind: "model";
      id: string;
      step: number;
      model: string;
      tokensIn: number | null;
      tokensOut: number | null;
      startedAt: number;
      durationMs: number | null;
    }
  | {
      kind: "tool";
      id: string;
      tool: string;
      server: boolean;
      args: Record<string, unknown>;
      result?: string;
      durationMs?: number;
      isError?: boolean;
    }
  | {
      kind: "sandbox";
      id: string;
      code: string;
      stdout: string;
      stderr: string;
      exitCode: number | null;
      durationMs: number;
      artifacts: Artifact[];
    }
  | { kind: "artifact"; id: string; artifact: Artifact }
  | { kind: "refusal"; id: string; reason: RefusalReason; message: string };

export interface RunState {
  text: string;
  steps: RunStep[];
  tokensIn: number;
  tokensOut: number;
  costEur: number | null;
  costUsd: number | null;
  artifacts: Artifact[];
  done: boolean;
  error: string | null;
  traceUrl: string | null;
  stopReason: string | null;
}

export const EMPTY_RUN: RunState = {
  text: "",
  steps: [],
  tokensIn: 0,
  tokensOut: 0,
  costEur: null,
  costUsd: null,
  artifacts: [],
  done: false,
  error: null,
  traceUrl: null,
  stopReason: null,
};

/** Fold one SSE frame of a test turn into the turn's state; `now` times the model calls. */
export function applyEvent(run: RunState, event: AgentTestEvent, now: number): RunState {
  switch (event.type) {
    case "text_delta":
      return { ...run, text: run.text + event.text };
    case "model_call":
      return {
        ...run,
        steps: [
          ...run.steps,
          {
            kind: "model",
            id: `model-${event.step}`,
            step: event.step,
            model: event.model,
            tokensIn: null,
            tokensOut: null,
            startedAt: now,
            durationMs: null,
          },
        ],
      };
    case "usage":
      return {
        ...run,
        tokensIn: event.total.input_tokens,
        tokensOut: event.total.output_tokens,
        costEur: event.total.cost_eur,
        costUsd: event.total.cost_usd,
        steps: run.steps.map((step) =>
          step.kind === "model" && step.step === event.step
            ? {
                ...step,
                tokensIn: event.input_tokens,
                tokensOut: event.output_tokens,
                durationMs: step.durationMs ?? now - step.startedAt,
              }
            : step,
        ),
      };
    case "tool_call":
      return {
        ...run,
        steps: [
          ...run.steps,
          { kind: "tool", id: event.id, tool: event.name, server: event.server ?? false, args: event.input },
        ],
      };
    case "tool_result":
      return {
        ...run,
        steps: run.steps.map((step) =>
          step.kind === "tool" && step.id === event.id
            ? { ...step, result: event.content, durationMs: event.duration_ms, isError: event.is_error }
            : step,
        ),
      };
    case "sandbox_run": {
      const step: RunStep = {
        kind: "sandbox",
        id: event.id,
        code: event.code,
        stdout: event.stdout,
        stderr: event.stderr,
        exitCode: event.exit_code,
        durationMs: event.duration_ms,
        artifacts: [],
      };
      // run_python arrives as a tool call first: the sandbox run replaces it.
      const index = run.steps.findIndex((s) => s.kind === "tool" && s.id === event.id);
      const steps = [...run.steps];
      if (index >= 0) steps[index] = step;
      else steps.push(step);
      return { ...run, steps };
    }
    case "artifact": {
      const artifact = { name: event.name, mime: event.mime, url: event.url };
      const lastSandbox = run.steps.findLastIndex((s) => s.kind === "sandbox");
      const steps = [...run.steps];
      if (lastSandbox >= 0) {
        const sandbox = steps[lastSandbox] as Extract<RunStep, { kind: "sandbox" }>;
        steps[lastSandbox] = { ...sandbox, artifacts: [...sandbox.artifacts, artifact] };
      } else {
        steps.push({ kind: "artifact", id: `artifact-${steps.length}`, artifact });
      }
      return { ...run, steps, artifacts: [...run.artifacts, artifact] };
    }
    case "refusal":
      return {
        ...run,
        steps: [
          ...run.steps,
          { kind: "refusal", id: `refusal-${run.steps.length}`, reason: event.reason, message: event.message },
        ],
      };
    case "done":
      return {
        ...run,
        done: true,
        stopReason: event.stop_reason,
        traceUrl: event.trace_url ?? null,
        error: event.stop_reason === "error" ? (event.message ?? "error") : run.error,
        steps: run.steps.map((step) =>
          step.kind === "model" && step.durationMs === null ? { ...step, durationMs: now - step.startedAt } : step,
        ),
      };
  }
}
