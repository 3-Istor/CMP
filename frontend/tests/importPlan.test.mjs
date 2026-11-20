import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const dependency = createRequire(new URL("../package.json", import.meta.url));
const ts = dependency("typescript");
const root = new URL("../", import.meta.url);

function load(file) {
  const source = readFileSync(new URL(file, root), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInThisContext(`(function (exports, require) {${outputText}\n})`)(exports, () => ({}));
  return exports;
}

const importPlan = load("src/lib/importPlan.ts");
const epitweet = JSON.parse(
  readFileSync(new URL("../backend/tests/fixtures/imports/epitweet-plan.json", root), "utf8"),
);

function withPostgres(...consumers) {
  const plan = structuredClone(epitweet);
  plan.backing.push({
    name: "postgres",
    kind: "postgres",
    image: "postgres:16",
    supported: true,
    managed: "postgres",
    port: 5432,
    resources: { requests_mem_mib: 256, limits_mem_mib: 512, cpu: "100m" },
    credentials_found: [],
  });
  for (const from of consumers) {
    plan.links.push({ from, to: "postgres", port: 5432, alias: "postgres", evidence: [] });
  }
  return plan;
}

test("a managed postgres is no problem and counts for no container memory", () => {
  const plan = withPostgres("user-service");
  const edits = importPlan.emptyEdits();

  const problems = importPlan.planProblems(plan, edits);
  const gauge = importPlan.capacityGauge(plan, edits, {
    project_max_mib: 100000,
    namespace_max_mib: 4096,
    other_imports_mib: 0,
    backing_requests_mib: {},
  });

  assert.deepEqual(problems, []);
  assert.equal(
    gauge.requestedMiB,
    importPlan.capacityGauge(epitweet, edits, {
      project_max_mib: 100000,
      namespace_max_mib: 4096,
      other_imports_mib: 0,
      backing_requests_mib: {},
    }).requestedMiB,
  );
});

test("a managed postgres shared by two apps is a problem", () => {
  const plan = withPostgres("user-service", "post-service");

  const problems = importPlan.planProblems(plan, importPlan.emptyEdits());

  assert.deepEqual(problems, [{ code: "sharedDatabase", app: "postgres" }]);
});

test("the only app linking to a managed postgres owns its database", () => {
  const plan = withPostgres("user-service");

  const owner = importPlan.managedDatabaseOf(plan, importPlan.emptyEdits(), "user-service");

  assert.equal(owner, "postgres");
});

test("the pooler toggle follows a renamed app", () => {
  const plan = withPostgres("user-service");
  let edits = importPlan.planEditsReducer(
    importPlan.emptyEdits(),
    { type: "setPooler", app: "user-service", value: true },
    plan,
  );

  edits = importPlan.planEditsReducer(edits, { type: "rename", app: "user-service", name: "users" }, plan);

  assert.deepEqual(edits.pooler, { users: true });
});

test("keeping an app awake is an edit, putting it back to sleep removes it", () => {
  let edits = importPlan.planEditsReducer(
    importPlan.emptyEdits(),
    { type: "setOffhours", app: "redis", value: false },
    epitweet,
  );
  assert.deepEqual(edits.offhours, { redis: false });

  edits = importPlan.planEditsReducer(edits, { type: "setOffhours", app: "redis", value: true }, epitweet);

  assert.deepEqual(edits.offhours, {});
});
