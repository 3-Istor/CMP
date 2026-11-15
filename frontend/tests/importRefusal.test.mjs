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

const { importRefusal } = load("src/lib/importRefusal.ts");
const apiError = (detail) => new Error(JSON.stringify({ detail }));
const pointer = {
  run_id: "run-2",
  kind: "analyze",
  status: "succeeded",
  api_path: "/api/projects/shop/imports/runs/run-2",
  ui_path: "/projects/shop/import?run=run-2",
};

test("a refusal caused by another run carries that run", () => {
  const refusal = importRefusal(
    apiError({ message: "The analysis changed since you reviewed it: review again", latest_run: pointer }),
  );

  assert.deepEqual(refusal, {
    message: "The analysis changed since you reviewed it: review again",
    latestRun: pointer,
  });
});

test("a plain refusal keeps its message and points nowhere", () => {
  const refusal = importRefusal(apiError("Invalid branch name"));

  assert.deepEqual(refusal, { message: "Invalid branch name", latestRun: null });
});

test("a pointer outside the project pages is ignored", () => {
  const refusal = importRefusal(
    apiError({ message: "busy", latest_run: { ...pointer, ui_path: "https://evil.example/" } }),
  );

  assert.equal(refusal.latestRun, null);
});

test("a body that is not JSON is shown as it is", () => {
  const refusal = importRefusal(new Error("Bad Gateway"));

  assert.deepEqual(refusal, { message: "Bad Gateway", latestRun: null });
});
