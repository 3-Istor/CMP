import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const dependency = createRequire(new URL("../package.json", import.meta.url));
const ts = dependency("typescript");
const root = new URL("../", import.meta.url);

// The modules only import types, which the transpiler erases.
function load(file) {
  const source = readFileSync(new URL(file, root), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  // This realm, so deepEqual compares arrays and objects across the boundary.
  vm.runInThisContext(`(function (exports, require) {${outputText}\n})`)(exports, () => ({}));
  return exports;
}

const topology = load("src/lib/topology.ts");
const importPlan = load("src/lib/importPlan.ts");
const plan = JSON.parse(
  readFileSync(new URL("../backend/tests/fixtures/imports/epitweet-plan.json", root), "utf8"),
);

const node = (id, fields = {}) => ({
  id,
  app: id,
  kind: "service",
  components: ["app"],
  public: false,
  hostname: null,
  health: "healthy",
  sync: "synced",
  asleep: false,
  digest: null,
  imported: true,
  backing_kind: null,
  ports: [{ name: "http", port: 8080 }],
  linkable: true,
  deployment_id: 1,
  in_registry: true,
  ...fields,
});

const edge = (from, to, state, declared) => ({
  id: `${from}->${to}`,
  from,
  to,
  port: declared ? 8080 : null,
  alias: declared ? to : null,
  declared,
  observed: null,
  state,
});

// ── Edge states ─────────────────────────────────────────────────────────────

test("each edge state draws as the design says", () => {
  const drawn = Object.fromEntries(
    ["declared_observed", "declared_only", "observed_only", "denied"].map((state) => {
      const visual = topology.edgeVisual(state);
      return [state, [visual.stroke, Boolean(visual.dash)]];
    }),
  );

  assert.deepEqual(drawn, {
    declared_observed: ["var(--edge-observed)", false],
    declared_only: ["var(--edge-idle)", true],
    observed_only: ["var(--edge-undeclared)", false],
    denied: ["var(--edge-denied)", false],
  });
});

test("an undeclared flow between two imported apps offers to declare it", () => {
  const graph = topology.liveGraph({
    project: "shop",
    nodes: [node("api"), node("db")],
    edges: [edge("api", "db", "observed_only", false)],
    observed_available: true,
    generated_at: "",
  });
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));

  assert.deepEqual(topology.edgeActions(graph.edges[0], nodes, true), ["declare"]);
});

test("an undeclared flow from a template app offers nothing", () => {
  const graph = topology.liveGraph({
    project: "shop",
    nodes: [node("web", { kind: "static", linkable: false, imported: false }), node("api")],
    edges: [edge("web", "api", "observed_only", false)],
    observed_available: true,
    generated_at: "",
  });
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));

  assert.deepEqual(topology.edgeActions(graph.edges[0], nodes, true), []);
});

test("a declared edge offers to unlink, a denied undeclared one offers nothing", () => {
  const nodes = new Map([["api", node("api")], ["db", node("db")]]);
  const actions = [
    topology.edgeActions(topology.liveGraph({ nodes: [], edges: [edge("api", "db", "declared_only", true)] }).edges[0], nodes, true),
    topology.edgeActions(topology.liveGraph({ nodes: [], edges: [edge("api", "db", "denied", false)] }).edges[0], nodes, true),
  ];

  assert.deepEqual(actions, [["unlink"], []]);
});

test("a reader gets no edge action", () => {
  const nodes = new Map([["api", node("api")], ["db", node("db")]]);
  const graph = topology.liveGraph({ nodes: [], edges: [edge("api", "db", "observed_only", false)] });

  assert.deepEqual(topology.edgeActions(graph.edges[0], nodes, false), []);
});

test("public apps get an entry edge from the internet node in the first column", () => {
  const graph = topology.liveGraph({
    nodes: [node("web", { kind: "static", public: true }), node("db", { kind: "backing" })],
    edges: [],
  });

  assert.deepEqual(
    graph.nodes.map((n) => [n.id, n.rank]),
    [["internet", 0], ["web", 1], ["db", 3]],
  );
});

test("alias checks mirror the registry validator", () => {
  assert.deepEqual(
    ["user-service", "vault", "api-canary", "9lives", "Api"].map((alias) =>
      topology.aliasProblem(alias, "api"),
    ),
    [null, "reserved", "own", "format", "format"],
  );
});

test("the draft graph stages every plan app and link next to the live apps", () => {
  const graph = topology.draftGraph(plan, importPlan.emptyEdits(), {
    nodes: [node("legacy")],
    edges: [],
  });

  const drafts = graph.nodes.filter((n) => n.draft);
  assert.equal(drafts.length, plan.services.length + plan.backing.length);
  assert.equal(graph.edges.filter((e) => e.state === "draft").length, plan.links.length);
});

test("a staged app named like a template app is flagged as a conflict", () => {
  const graph = topology.draftGraph(plan, importPlan.emptyEdits(), {
    nodes: [node("redis", { kind: "static", imported: false, linkable: false })],
    edges: [],
  });

  assert.equal(graph.nodes.find((n) => n.id === "plan:redis").conflict, true);
});

test("an imported app the plan deploys again is drawn once, as staged", () => {
  const graph = topology.draftGraph(plan, importPlan.emptyEdits(), {
    nodes: [node("redis")],
    edges: [],
  });

  assert.deepEqual(
    graph.nodes.filter((n) => n.name === "redis").map((n) => [n.id, n.conflict]),
    [["plan:redis", false]],
  );
});

// ── Plan edits ──────────────────────────────────────────────────────────────

const reduce = (actions, edits = importPlan.emptyEdits()) =>
  actions.reduce((state, action) => importPlan.planEditsReducer(state, action, plan), edits);

test("a rename carries the app's other edits to its new name", () => {
  const edits = reduce([
    { type: "setPublic", app: "user-service", value: true },
    { type: "setPort", app: "user-service", port: 9090 },
    { type: "rename", app: "user-service", name: "users" },
  ]);

  assert.deepEqual([edits.rename, edits.public, edits.port], [
    { "user-service": "users" },
    { users: true },
    { users: 9090 },
  ]);
});

test("setting a value back to the plan's removes the edit", () => {
  const edits = reduce([
    { type: "setPublic", app: "frontend", value: false },
    { type: "setPublic", app: "frontend", value: true },
    { type: "rename", app: "redis", name: "cache" },
    { type: "rename", app: "redis", name: "" },
  ]);

  assert.deepEqual([edits.public, edits.rename], [{}, {}]);
});

test("a port edit moves the plan's probe and an edited probe on that port", () => {
  const edits = reduce([
    { type: "setProbe", app: "user-service", probe: { kind: "http", path: "/health", port: 8081 } },
    { type: "setPort", app: "user-service", port: 9000 },
  ]);

  assert.deepEqual(importPlan.effectiveService(plan, edits, "user-service").probe, {
    kind: "http",
    path: "/health",
    port: 9000,
  });
});

test("a tcp probe drops its path", () => {
  const edits = reduce([
    { type: "setProbe", app: "user-service", probe: { kind: "tcp", path: "/x", port: 8081 } },
  ]);

  assert.equal(edits.probe["user-service"].path, null);
});

test("toggling a link twice restores it", () => {
  const edits = reduce([
    { type: "toggleLink", from: "user-service", to: "redis" },
    { type: "toggleLink", from: "user-service", to: "redis" },
  ]);

  assert.deepEqual(edits.drop_links, []);
});

test("the plan as analysed has nothing to block it", () => {
  assert.deepEqual(importPlan.planProblems(plan, importPlan.emptyEdits()), []);
});

test("problems the API would refuse are found before applying", () => {
  const edits = reduce([
    { type: "rename", app: "frontend", name: "redis" },
    { type: "setMemory", app: "user-service", requests: 512, limits: 256 },
    { type: "setProbe", app: "post-service", probe: { kind: "http", path: "", port: 8082 } },
  ]);

  assert.deepEqual(
    importPlan.planProblems(plan, edits).map((p) => `${p.code}:${p.app ?? ""}`).sort(),
    ["duplicate:redis", "memory:user-service", "probePath:post-service"],
  );
});

test("an app name taken in the project is a duplicate", () => {
  const problems = importPlan.planProblems(plan, importPlan.emptyEdits(), ["frontend"]);

  assert.deepEqual(problems, [{ code: "duplicate", app: "frontend" }]);
});

test("the gauge counts catalogue presets for backing apps and skips excluded apps", () => {
  const capacity = {
    project_max_mib: 1000,
    namespace_max_mib: 2048,
    other_imports_mib: 100,
    backing_requests_mib: { mongodb: 256, redis: 64, neo4j: 512, elasticsearch: 1024 },
  };
  const all = importPlan.capacityGauge(plan, importPlan.emptyEdits(), capacity);
  const fewer = importPlan.capacityGauge(
    plan,
    reduce([{ type: "toggleExclude", app: "elasticsearch" }]),
    capacity,
  );

  assert.equal(all.requestedMiB - fewer.requestedMiB, 1024);
  assert.equal(all.over, true);
});

// ── Build progress ──────────────────────────────────────────────────────────

const api = { app: "api", kind: "service", status: "pending", public: true };

test("a claimed build shows the build stage running", () => {
  const stages = importPlan.serviceStages({ kind: "build", status: "claimed", report: null }, api);

  assert.deepEqual(Object.values(stages), ["active", "pending", "pending", "pending", "pending"]);
});

test("a scan failure marks build done and scan failed", () => {
  const stages = importPlan.serviceStages(
    { kind: "build", status: "failed", report: { stage: "scan", error: "CRITICAL" } },
    api,
  );

  assert.deepEqual([stages.build, stages.scan, stages.publish], ["done", "failed", "pending"]);
});

test("an unreachable public app passed deploy but failed its smoke test", () => {
  const stages = importPlan.serviceStages(
    {
      kind: "build",
      status: "failed",
      report: { images: { api: { digest: "sha256:a" } }, states: { api: "unreachable" } },
    },
    api,
  );

  assert.deepEqual(Object.values(stages), ["done", "done", "done", "done", "failed"]);
});

test("a backing app skips the build stages and a private app skips the smoke test", () => {
  const stages = importPlan.serviceStages(
    { kind: "build", status: "running", report: { images: {} } },
    { app: "db", kind: "backing", status: "deploying", public: false },
  );

  assert.deepEqual(Object.values(stages), ["skipped", "skipped", "skipped", "active", "skipped"]);
});
