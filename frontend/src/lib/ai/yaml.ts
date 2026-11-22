import type { AgentBlock } from "@/types/ai";

const PLAIN = /^[A-Za-z0-9_.\-/@:]+$/;

function scalar(value: string | number | boolean): string {
  if (typeof value !== "string") return String(value);
  return PLAIN.test(value) && !/^(true|false|null|yes|no|\d.*)$/i.test(value) ? value : JSON.stringify(value);
}

/** The `agent:` block as the CMP writes it in deploy/values-<agent>.yaml. */
export function agentValuesYaml(spec: AgentBlock): string {
  const lines = ["agent:"];
  if (spec.template) lines.push(`  template: ${scalar(spec.template)}`);
  lines.push(`  description: ${scalar(spec.description)}`);
  lines.push(`  model: ${scalar(spec.model)}`);
  lines.push(`  effort: ${scalar(spec.effort)}`);
  lines.push(`  maxSteps: ${spec.maxSteps}`);
  lines.push(
    spec.trigger.cron
      ? `  trigger: {type: ${spec.trigger.type}, cron: ${JSON.stringify(spec.trigger.cron)}}`
      : `  trigger: {type: ${spec.trigger.type}}`,
  );
  const instructions = spec.instructions.trim();
  if (instructions) {
    lines.push("  instructions: |");
    for (const line of instructions.split("\n")) lines.push(line ? `    ${line}` : "");
  }
  lines.push(`  tools: [${spec.tools.map(scalar).join(", ")}]`);
  if (spec.calls?.length) lines.push(`  calls: [${spec.calls.map(scalar).join(", ")}]`);
  return lines.join("\n");
}
