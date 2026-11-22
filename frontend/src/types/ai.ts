// Shapes of the CMP AI API (backend routers/ai.py), snake_case as served.

export type AiModelId = "claude-haiku-5-5" | "claude-sonnet-5-5" | (string & {});
export type AgentIsolation = "runc" | "gvisor" | "kata";
export type AgentStatus = "active" | "suspended" | "error" | "pending";
export type AgentTriggerType = "chat" | "event" | "schedule" | "a2a";
export type AgentEffort = "low" | "medium" | "high";
export type AgentTemplateId =
  | "assistant"
  | "docs-qa"
  | "data-analyst"
  | "app-bot"
  | "ops-diagnosis"
  | "pr-review";

/** Badges a tool carries in the registry; the Rule of Two is computed from them. */
export type ToolRisk =
  | "untrusted_content"
  | "private_data"
  | "writes"
  | "external_send"
  | "executes_code";

export type AiBackendStatus = "ok" | "unavailable" | "spend_limit_reached";

export interface AiAppAccess {
  app: string;
  enabled: boolean;
  daily_token_budget: number;
  client_id?: string;
}

export interface PullRequestRef {
  number: number;
  url: string;
  state?: "open" | "merged" | "closed";
}

export interface AgentTrigger {
  type: AgentTriggerType;
  cron?: string;
}

export interface AgentRisk {
  private_data: boolean;
  untrusted_content: boolean;
  external_send: boolean;
  executes_code: boolean;
  count: number;
  verdict: "ok" | "blocked";
  isolation: AgentIsolation;
}

export interface Agent {
  name: string;
  project: string;
  template: AgentTemplateId | null;
  role?: "reader" | "actor" | null;
  description: string;
  owner: string | null;
  status: AgentStatus;
  source?: "cmp" | "yaml";
  trigger: AgentTrigger;
  model: AiModelId;
  models?: AiModelId[];
  tools: string[];
  isolation: AgentIsolation;
  callers: string[];
  calls: string[];
  daily_token_budget: number;
  client_id?: string;
  repo?: string | null;
  values_path?: string | null;
  pull_request?: PullRequestRef | null;
  risk?: AgentRisk;
  usage_today?: { tokens: number; cost_usd: number; cost_eur: number };
  updated_at?: string | null;
}

/** The `agent:` block of deploy/values-<agent>.yaml. */
export interface AgentBlock {
  template?: AgentTemplateId;
  description: string;
  model: AiModelId;
  effort: AgentEffort;
  maxSteps: number;
  trigger: AgentTrigger;
  instructions: string;
  tools: string[];
  calls?: string[];
}

export interface AgentDetail extends Agent {
  declaration?: { agent?: Partial<AgentBlock> } | Partial<AgentBlock> | null;
  declaration_source?: "main" | "pull_request";
}

export interface ProjectAi {
  project: string;
  enabled: boolean;
  can_enable: boolean;
  stake?: string;
  models: AiModelId[];
  available_models?: AiModelId[];
  web_search: boolean;
  daily_token_budget: number;
  apps: AiAppAccess[];
  agents?: Agent[];
  backend?: { name: string; status: AiBackendStatus };
}

export type RefusalReason = "budget" | "policy" | "ratelimit" | "guardrail";

export interface AiUsageItem {
  azp: string;
  kind: "agent" | "app" | "other";
  name: string;
  tokens_in: number;
  tokens_out: number;
  tokens_cache?: number;
  requests: number;
  errors: number;
  refused: Partial<Record<RefusalReason, number>>;
  refused_total: number;
  tool_calls?: number;
  budget: number;
  cost_usd: number;
  cost_eur: number;
}

export interface AiUsage {
  period: string;
  items: AiUsageItem[];
}

export interface AgentUsage extends AiUsageItem {
  series: { t: string; tokens: number; cost_usd?: number }[];
}

export interface AiBackend {
  name: string;
  kind: string;
  status: AiBackendStatus;
  spend_usd: number;
  spend_limit_usd: number;
}

export interface AiGlobalBudget {
  spend_usd: number;
  budget_usd: number;
  ratio: number;
  status: "ok" | "warning" | "exhausted";
}

export interface AiTool {
  name: string;
  source: string;
  description?: string;
  badges: ToolRisk[];
}

export interface AgentTemplate {
  id: AgentTemplateId;
  title: string;
  description: string;
  icon: string;
  defaults: AgentBlock;
  /** `name` for platform tools, `match` a glob over <mcp app>__<tool> for project tools. */
  suggested_tools: { name?: string; match?: string; badges: ToolRisk[]; why?: string }[];
  risk_notes: string[];
  /** app-bot only; an empty reader block means the top-level defaults. */
  pair: { reader: Partial<AgentBlock>; actor: Partial<AgentBlock> } | null;
}

export interface AgentDraft {
  template: AgentTemplateId;
  name?: string;
  agent: AgentBlock;
  actor: AgentBlock | null;
  errors: string[];
}

export interface CreateAgentRequest {
  template: AgentTemplateId;
  name: string;
  repo_app: string;
  description: string;
  owner: string | null;
  model: AiModelId;
  effort: AgentEffort;
  max_steps: number;
  trigger: AgentTrigger;
  instructions: string;
  tools: string[];
  isolation: AgentIsolation | null;
  callers: string[];
  calls: string[];
  daily_token_budget: number;
  output: null;
  actor?: { instructions: string; tools: string[]; daily_token_budget: number };
}

export interface CreateAgentResult {
  agents: AgentDetail[];
  pull_request: PullRequestRef | null;
}

export interface AgentTrace {
  trace_id: string;
  start: string;
  duration_ms: number;
  spans: number;
  errors: number;
  root_operation: string;
}

export interface AgentTraces {
  items: AgentTrace[];
  grafana_url: string | null;
}

export interface AdminMcpServer {
  name: string;
  project: string | null;
  source: string;
  status: "approved" | "pending" | "retired";
  tools: { name: string; badges: ToolRisk[] }[];
}

export interface Items<T> {
  items: T[];
}

/** SSE frames of POST /projects/{p}/agents/{a}/test: `event:` is the type, `data:` the JSON. */
export type AgentTestEvent =
  | { type: "model_call"; step: number; model: AiModelId; effort?: AgentEffort; max_tokens?: number }
  | { type: "text_delta"; step: number; text: string }
  | { type: "tool_call"; step: number; id: string; name: string; input: Record<string, unknown>; server?: boolean }
  | {
      type: "tool_result";
      step: number;
      id: string;
      name: string;
      is_error: boolean;
      content: string;
      duration_ms: number;
    }
  | {
      type: "sandbox_run";
      step: number;
      id: string;
      code: string;
      stdout: string;
      stderr: string;
      exit_code: number | null;
      duration_ms: number;
    }
  | { type: "artifact"; step: number; name: string; mime: string; url: string }
  | { type: "refusal"; step: number; reason: RefusalReason; status?: number; message: string }
  | {
      type: "usage";
      step: number;
      input_tokens: number;
      output_tokens: number;
      cache_read_tokens?: number;
      cost_usd: number;
      total: {
        input_tokens: number;
        output_tokens: number;
        cache_read_tokens?: number;
        cost_usd: number;
        cost_eur: number;
      };
    }
  | {
      type: "done";
      stop_reason: "end_turn" | "max_steps" | "refusal" | "error";
      steps?: number;
      duration_ms?: number;
      trace_id?: string | null;
      trace_url?: string | null;
      message?: string | null;
    };
