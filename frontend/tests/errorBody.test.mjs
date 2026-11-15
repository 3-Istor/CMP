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

const { errorBody } = load("src/lib/errorBody.ts");
const cloudflarePage =
  '<!DOCTYPE html>\n<html lang="en-US"><head><title>3istor.com | 502: Bad gateway</title></head><body>Ray ID: a59ff81fee68ebac</body></html>';

test("a proxy error page becomes a short retry message", () => {
  const text = errorBody(502, "text/html; charset=UTF-8", cloudflarePage);

  assert.equal(text, "The server is temporarily unavailable (HTTP 502). Try again in a moment.");
});

test("an HTML page without a content type is still recognised", () => {
  const text = errorBody(504, null, cloudflarePage);

  assert.doesNotMatch(text, /<html/i);
});

test("a JSON error from the backend is kept as is", () => {
  const body = JSON.stringify({ detail: "main moved, reload the choices." });

  assert.equal(errorBody(409, "application/json", body), body);
});
