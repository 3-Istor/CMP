import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const dependency = createRequire(new URL("../package.json", import.meta.url));
const ts = dependency("typescript");

function load(file) {
  const source = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInThisContext(`(function (exports, require) {${outputText}\n})`)(exports, () => ({}));
  return exports;
}

const {
  sortByAttention,
  attentionCount,
  kindLabel,
  matchesKind,
  toPoints,
  compactDuration,
  rollbackTarget,
} = load("src/lib/appHealth.ts");

const UNITS = { s: "s", m: "m", h: "h", d: "d" };

test("apps that need attention come first, then by name", () => {
  const rows = [
    { name: "b-ok", status: "healthy" },
    { name: "a-ok", status: "healthy" },
    { name: "web", status: "degraded" },
    { name: "db", status: "down" },
    { name: "new", status: "unknown" },
  ];

  const sorted = sortByAttention(rows).map((r) => r.name);

  assert.deepEqual(sorted, ["db", "web", "new", "a-ok", "b-ok"]);
});

test("sorting leaves the input list untouched", () => {
  const rows = [{ name: "b", status: "healthy" }, { name: "a", status: "down" }];

  sortByAttention(rows);

  assert.equal(rows[0].name, "b");
});

test("only down and degraded apps count as needing attention", () => {
  const rows = [{ status: "down" }, { status: "degraded" }, { status: "unknown" }, { status: "healthy" }];

  assert.equal(attentionCount(rows), 2);
});

test("a web app named like a front end reads as Frontend", () => {
  assert.equal(kindLabel("web", "frontend", "import-service"), "frontend");
  assert.equal(kindLabel("web", "landing-page", null), "frontend");
});

test("a web app without a front-end hint reads as Service", () => {
  assert.equal(kindLabel("web", "timeline-service", "import-service"), "service");
});

test("datastores and workers keep their kind whatever the name says", () => {
  assert.equal(kindLabel("datastore", "web-cache"), "datastore");
  assert.equal(kindLabel("worker", "frontend-builder"), "worker");
});

test("the type filter keeps every app on all and only the kind otherwise", () => {
  assert.equal(matchesKind("datastore", "all"), true);
  assert.equal(matchesKind("datastore", "web"), false);
  assert.equal(matchesKind("worker", "worker"), true);
});

test("chart points read a missing or short series as gaps", () => {
  const points = toPoints([1, 2], { rps: [0.5], err: undefined });

  assert.deepEqual(points, [
    { t: 1, rps: 0.5, err: null },
    { t: 2, rps: null, err: null },
  ]);
});

test("durations keep two units at most", () => {
  assert.equal(compactDuration(42, UNITS), "42s");
  assert.equal(compactDuration(102, UNITS), "1m 42s");
  assert.equal(compactDuration(120, UNITS), "2m");
  assert.equal(compactDuration(39 * 3600 + 1200, UNITS), "39h");
  assert.equal(compactDuration(5 * 86400, UNITS), "5d");
});

test("roll back targets the newest replaced version older than the live one", () => {
  const deploys = [
    { version: "1.1.0", at: "", outcome: "live" },
    { version: "0.2.0", at: "", outcome: "replaced" },
    { version: "0.1.0", at: "", outcome: "replaced" },
  ];
  const candidates = [
    { version: "0.2.0", promotionId: 7 },
    { version: "0.1.0", promotionId: 5 },
  ];

  assert.deepEqual(rollbackTarget(deploys, candidates), { version: "0.2.0", promotionId: 7 });
});

test("no roll back when the previous version cannot be promoted again", () => {
  const deploys = [
    { version: "1.1.0", at: "", outcome: "live" },
    { version: "0.2.0", at: "", outcome: "replaced" },
  ];

  assert.equal(rollbackTarget(deploys, [{ version: "0.2.0", promotionId: null }]), null);
});

test("no roll back without a live version", () => {
  const deploys = [{ version: "1.1.0", at: "", outcome: "failed" }, { version: "0.2.0", at: "", outcome: "replaced" }];

  assert.equal(rollbackTarget(deploys, [{ version: "0.2.0", promotionId: 3 }]), null);
});
