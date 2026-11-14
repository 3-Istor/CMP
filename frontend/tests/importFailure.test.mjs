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

const { failurePlan, blockingCount } = load("src/lib/importFailure.ts");
const netty = {
  app: "user-service",
  id: "CVE-2026-75595",
  package: "io.netty:netty-handler",
  installed: "4.1.90.Final",
  fixed: "4.1.137.Final",
  severity: "CRITICAL",
};
const failure = (fields) => ({
  cause: "scan_blocked",
  stage: "scan",
  message: "",
  apps: ["user-service"],
  vulnerabilities: [netty, { ...netty, id: "CVE-2023-7272" }],
  upgrades: [],
  log_excerpt: null,
  actions: ["fix_in_repo", "retry"],
  rebuilds_on_push: false,
  ...fields,
});

test("a scan block asks for a fix in the repo, then a rebuild", () => {
  const plan = failurePlan(failure(), "build");

  assert.deepEqual(plan, { buttons: ["rebuild"], hints: ["fix_in_repo"] });
});

test("a scan block says the push rebuilds when the import rebuilds on push", () => {
  const plan = failurePlan(failure({ rebuilds_on_push: true }), "build");

  assert.deepEqual(plan.hints, ["fix_in_repo", "push_rebuilds"]);
});

test("a retry of an analysis starts a new analysis, once", () => {
  const plan = failurePlan(failure({ actions: ["retry", "reanalyze"] }), "analyze");

  assert.deepEqual(plan.buttons, ["analyze"]);
});

test("a stale plan offers a re-analysis of a failed build", () => {
  const plan = failurePlan(failure({ actions: ["reanalyze"] }), "build");

  assert.deepEqual(plan, { buttons: ["analyze"], hints: [] });
});

test("an internal error points to the platform team besides the retry", () => {
  const plan = failurePlan(failure({ actions: ["retry", "contact_platform"] }), "build");

  assert.deepEqual(plan, { buttons: ["rebuild"], hints: ["contact_platform"] });
});

test("each app counts only its own blocking vulnerabilities", () => {
  const counts = ["user-service", "post-service"].map((app) => blockingCount(failure(), app));

  assert.deepEqual(counts, [2, 0]);
});
