import type {
  Agent,
  AgentDraft,
  AgentTemplate,
  AgentTestEvent,
  AgentUsage,
  AiTool,
  ProjectAi,
} from "../../src/types/ai";

const day = (base: number) => Array.from({ length: 24 }, (_, i) => Math.round(base * (0.4 + Math.abs(Math.sin(i / 3)))));

export const tools: AiTool[] = [
  { name: "epitweet-mcp__get_thread", source: "epitweet-mcp", description: "Lit un fil public", badges: ["untrusted_content"] },
  { name: "epitweet-mcp__search_posts", source: "epitweet-mcp", description: "Cherche des posts publics", badges: ["untrusted_content"] },
  { name: "epitweet-mcp__reply_as_grok", source: "epitweet-mcp", description: "Répond dans le fil, au nom de @grok", badges: ["writes"] },
  { name: "cmp__get_app_logs", source: "cmp", description: "Lit les logs d'une app du projet", badges: ["untrusted_content", "private_data"] },
  { name: "run_python", source: "platform", description: "Exécute du Python dans un bac à sable", badges: ["executes_code"] },
  { name: "web_search", source: "platform", description: "Recherche web d'Anthropic", badges: ["external_send", "untrusted_content"] },
];

const base: Agent = {
  name: "grok-reader",
  project: "demo",
  owner: "brian.perret",
  status: "active",
  isolation: "gvisor",
  template: "app-bot",
  role: "reader",
  source: "cmp",
  description: "Lit les mentions @grok, vérifie les faits et trace des graphiques",
  model: "claude-haiku-5-5",
  trigger: { type: "event" },
  tools: ["epitweet-mcp__get_thread", "epitweet-mcp__search_posts", "web_search", "run_python"],
  calls: ["grok-actor"],
  callers: ["app:grok-bridge", "user:members"],
  daily_token_budget: 300000,
  usage_today: { tokens: 41200, cost_usd: 0.065, cost_eur: 0.06 },
  updated_at: "2026-11-22T15:58:00Z",
  client_id: "agent-demo-grok-reader",
  pull_request: { number: 12, url: "https://github.com/3-Istor/epitweet/pull/12", state: "merged" },
};

export const agents: Agent[] = [
  base,
  {
    ...base,
    name: "grok-actor",
    isolation: "runc",
    description: "Publie la réponse de @grok dans le fil",
    trigger: { type: "a2a" },
    tools: ["epitweet-mcp__reply_as_grok"],
    calls: [],
    callers: ["agent:grok-reader"],
    role: "actor",
    daily_token_budget: 50000,
    usage_today: { tokens: 6100, cost_usd: 0.011, cost_eur: 0.01 },
    client_id: "agent-demo-grok-actor",
  },
  {
    ...base,
    name: "diag-deploiement",
    status: "suspended",
    isolation: "runc",
    template: "ops-diagnosis",
    description: "Explique pourquoi un déploiement échoue",
    trigger: { type: "chat" },
    tools: ["cmp__get_app_logs"],
    calls: [],
    role: null,
    daily_token_budget: 100000,
    usage_today: { tokens: 0, cost_usd: 0, cost_eur: 0 },
  },
];

export const projectAi: ProjectAi = {
  project: "demo",
  can_enable: true,
  stake: "demo",
  enabled: true,
  daily_token_budget: 2000000,
  models: ["claude-haiku-5-5", "claude-sonnet-5-5"],
  web_search: true,
  apps: [{ app: "demo-app", enabled: true, daily_token_budget: 50000, client_id: "app-demo-demo-app" }],
};

const assistantDefaults = {
  description: "Assistant du projet",
  model: "claude-haiku-5-5",
  effort: "low" as const,
  maxSteps: 8,
  trigger: { type: "chat" as const },
  instructions: "Réponds en français.",
  tools: [] as string[],
};

const block = (tools: string[], trigger: "chat" | "event" | "a2a" = "chat") => ({ ...assistantDefaults, trigger: { type: trigger }, tools });
const tpl = (
  id: AgentTemplate["id"],
  icon: string,
  title: string,
  description: string,
  suggested: AgentTemplate["suggested_tools"] = [],
): AgentTemplate => ({
  id,
  title,
  description,
  icon,
  defaults: block(suggested.flatMap((tool) => (tool.name ? [tool.name] : []))),
  suggested_tools: suggested,
  risk_notes: [],
  pair: null,
});

export const templates: AgentTemplate[] = [
  tpl("assistant", "sparkles", "Assistant", "Consignes, modèle et outils au choix"),
  tpl("docs-qa", "book-open-text", "Questions sur une doc", "Cherche dans une doc et cite ses sources", [
    { match: "*__search*", badges: ["private_data"] },
  ]),
  tpl("data-analyst", "chart-column", "Analyste de données", "Répond avec des chiffres et des graphiques", [
    { name: "run_python", badges: ["executes_code"] },
  ]),
  {
    ...tpl("app-bot", "bot", "Bot d'app, lecteur et acteur", "Déclenché par un événement, coupé en deux agents", [
      { name: "web_search", badges: ["untrusted_content", "external_send"] },
      { name: "run_python", badges: ["executes_code"] },
    ]),
    defaults: block(["epitweet-mcp__get_thread", "web_search", "run_python"], "event"),
    pair: { reader: {}, actor: block(["epitweet-mcp__reply_as_grok"], "a2a") },
  },
  tpl("ops-diagnosis", "stethoscope", "Diagnostic de déploiement", "Lit l'état et les logs, explique"),
  tpl("pr-review", "git-pull-request", "Revue de PR", "Lit une PR et la commente"),
];

export const draft: AgentDraft = {
  template: "app-bot",
  name: "grok",
  agent: { ...block(["epitweet-mcp__get_thread", "web_search", "run_python"], "event"), description: "Répond aux mentions @grok en vérifiant les faits" },
  actor: block(["epitweet-mcp__reply_as_grok"], "a2a"),
  errors: [],
};

const usage = (tokensIn: number, costEur: number): AgentUsage => ({
  azp: "agent-demo-grok-reader",
  kind: "agent",
  name: "grok-reader",
  tokens_in: tokensIn,
  tokens_out: 8200,
  requests: 57,
  errors: 1,
  refused: { budget: 0, policy: 2, ratelimit: 0, guardrail: 1 },
  refused_total: 3,
  budget: 300000,
  cost_usd: costEur * 1.08,
  cost_eur: costEur,
  series: day(2400).map((tokens, i) => ({ t: `2026-11-22T${String(i).padStart(2, "0")}:00:00Z`, tokens })),
});

const suspended = { ...base, status: "suspended" as const };

/** Answers for installMockApi: the AI pages of project "demo". */
export const AI_ROUTES: Record<string, { status?: number; json: unknown }> = {
  "/projects/demo/ai": { json: projectAi },
  "/projects/demo/ai/usage": {
    json: {
      period: "1d",
      items: [
        { ...usage(33000, 0.06), series: undefined },
        { azp: "app-demo-demo-app", kind: "app", name: "demo-app", tokens_in: 9000, tokens_out: 2100, requests: 12, errors: 0, refused: {}, refused_total: 0, budget: 50000, cost_usd: 0.011, cost_eur: 0.01 },
      ],
    },
  },
  "/projects/demo/ai/tools": { json: { items: tools } },
  "/ai/backends": { json: { items: [{ name: "anthropic", kind: "anthropic", status: "ok", spend_usd: 0.42, spend_limit_usd: 5 }] } },
  "/ai/budget": { json: { spend_usd: 0.42, budget_usd: 5, ratio: 0.084, status: "ok" } },
  "/ai/templates": { json: { items: templates } },
  "/ai/draft-agent": { json: draft },
  "/projects/demo/agents": { json: { items: agents } },
  "/admin/agents": { json: { items: agents } },
  "/admin/mcp-servers": {
    json: { items: [{ name: "epitweet-mcp", project: "demo", source: "app", status: "approved", tools: tools.slice(0, 3).map(({ name, badges }) => ({ name, badges })) }] },
  },
  "/projects/demo/agents/grok-reader": { json: base },
  "/projects/demo/agents/grok-reader/suspend": { json: suspended },
  "/projects/demo/agents/grok-reader/resume": { json: base },
  "/projects/demo/agents/grok-reader/usage": { json: usage(33000, 0.06) },
  "/projects/demo/agents/grok-actor/usage": { json: { ...usage(4000, 0.01), name: "grok-actor" } },
  "/projects/demo/agents/diag-deploiement/usage": { json: { ...usage(0, 0), series: [] } },
  "/projects/demo/agents/grok-reader/traces": {
    json: {
      items: [{ trace_id: "4bf92f3577b34da6a3ce929d0e0e4736", start: "2026-11-22T15:58:00Z", duration_ms: 8400, spans: 14, errors: 0, root_operation: "invoke_agent grok-reader" }],
      grafana_url: "https://grafana.example.invalid/explore",
    },
  },
};

export const PNG_1PX =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

export const testRun: AgentTestEvent[] = [
  { type: "model_call", step: 1, model: "claude-haiku-5-5", effort: "low", max_tokens: 1500 },
  { type: "tool_call", step: 1, id: "c1", name: "web_search", input: { query: "taux de chômage France 2026" }, server: true },
  { type: "tool_result", step: 1, id: "c1", name: "web_search", is_error: false, content: JSON.stringify({ results: [{ title: "INSEE", url: "https://insee.fr" }] }), duration_ms: 400 },
  { type: "tool_call", step: 1, id: "c2", name: "run_python", input: { code: "print(1)" } },
  { type: "usage", step: 1, input_tokens: 1820, output_tokens: 140, cost_usd: 0.002, total: { input_tokens: 1820, output_tokens: 140, cost_usd: 0.002, cost_eur: 0.0018 } },
  {
    type: "sandbox_run",
    step: 1,
    id: "c2",
    code: "import matplotlib.pyplot as plt\n# trace la série\nvalues = [7.1, 7.3, 7.4]\nplt.plot(values)\nplt.savefig('chart.png')\nprint(len(values))",
    stdout: "3\n",
    stderr: "",
    exit_code: 0,
    duration_ms: 2100,
  },
  { type: "artifact", step: 1, name: "chart.png", mime: "image/png", url: `data:image/png;base64,${PNG_1PX}` },
  { type: "tool_result", step: 1, id: "c2", name: "run_python", is_error: false, content: "3", duration_ms: 2100 },
  { type: "refusal", step: 2, reason: "policy", status: 403, message: "epitweet-mcp__reply_as_grok n'est pas autorisé pour cet agent" },
  { type: "model_call", step: 2, model: "claude-haiku-5-5" },
  { type: "text_delta", step: 2, text: "Le taux est de " },
  { type: "text_delta", step: 2, text: "7,4 %, selon l'INSEE." },
  { type: "usage", step: 2, input_tokens: 6410, output_tokens: 210, cost_usd: 0.0079, total: { input_tokens: 8230, output_tokens: 350, cost_usd: 0.0099, cost_eur: 0.0091 } },
  { type: "done", stop_reason: "end_turn", steps: 2, duration_ms: 3100, trace_id: "t2", trace_url: "https://grafana.example.invalid/t2", message: null },
];

/** SSE as the backend writes it: the type on the `event:` line, the rest as JSON. */
export function sse(events: AgentTestEvent[]): string {
  return events.map(({ type, ...data }) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}
