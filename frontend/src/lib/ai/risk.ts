import type { AgentIsolation, AiTool, ToolRisk } from "@/types/ai";

/** The three properties of the Rule of Two: an agent may hold two, never three. */
export type RuleOfTwoRisk = "private_data" | "untrusted_content" | "external";

export const RULE_OF_TWO: RuleOfTwoRisk[] = ["untrusted_content", "private_data", "external"];

export interface RiskProfile {
  executesCode: boolean;
  untrustedInput: boolean;
  privateData: boolean;
  /** Sends data out of the platform; a narrow write tool alone does not count (backend declaration.py). */
  external: boolean;
  lit: RuleOfTwoRisk[];
  blocked: boolean;
  isolation: AgentIsolation;
}

export type RiskAnswers = Pick<RiskProfile, "executesCode" | "untrustedInput" | "privateData" | "external">;

const ISOLATION_RANK: Record<AgentIsolation, number> = { runc: 0, gvisor: 1, kata: 2 };

export function strongerIsolation(a: AgentIsolation, b: AgentIsolation): AgentIsolation {
  return ISOLATION_RANK[a] >= ISOLATION_RANK[b] ? a : b;
}

export function toolRisks(names: string[], registry: AiTool[]): Set<ToolRisk> {
  const risks = new Set<ToolRisk>();
  for (const name of names) {
    for (const risk of registry.find((tool) => tool.name === name)?.badges ?? []) risks.add(risk);
  }
  return risks;
}

export function answersFromTools(names: string[], registry: AiTool[]): RiskAnswers {
  const risks = toolRisks(names, registry);
  return {
    executesCode: risks.has("executes_code"),
    untrustedInput: risks.has("untrusted_content"),
    privateData: risks.has("private_data"),
    external: risks.has("external_send"),
  };
}

/** Code always runs under gVisor at least; Kata is a stronger choice, never a minimum. */
export function minimumIsolation(answers: RiskAnswers): AgentIsolation {
  return answers.executesCode ? "gvisor" : "runc";
}

export function riskProfile(answers: RiskAnswers): RiskProfile {
  const lit: RuleOfTwoRisk[] = [];
  if (answers.untrustedInput) lit.push("untrusted_content");
  if (answers.privateData) lit.push("private_data");
  if (answers.external) lit.push("external");
  return { ...answers, lit, blocked: lit.length === 3, isolation: minimumIsolation(answers) };
}

/** Group the selected tools by the role they play on the capability map. */
export function toolColumns(names: string[], registry: AiTool[]) {
  const sources: AiTool[] = [];
  const actions: AiTool[] = [];
  for (const name of names) {
    const tool: AiTool = registry.find((t) => t.name === name) ?? {
      name,
      source: name.split("__")[0] ?? name,
      badges: [],
    };
    const acts = tool.badges.some((r) => r === "writes" || r === "external_send" || r === "executes_code");
    const reads = tool.badges.some((r) => r === "untrusted_content" || r === "private_data") || !acts;
    if (reads) sources.push(tool);
    if (acts) actions.push(tool);
  }
  return { sources, actions };
}
