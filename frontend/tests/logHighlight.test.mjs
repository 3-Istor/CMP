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

const { searchPattern, highlightParts, matches, parseFields } = load(
  "src/components/activity/logHighlight.tsx",
);
const { logsQueryFromParams, logsQueryToParams } = load(
  "src/components/activity/logsQuery.ts",
);

test("a literal search matches regex characters as text", () => {
  const { pattern } = searchPattern("a.b", false);

  assert.equal(matches("axb", pattern), false);
  assert.equal(matches("see a.b here", pattern), true);
});

test("an invalid regex is reported instead of throwing", () => {
  const result = searchPattern("time(out", true);

  assert.deepEqual(result, { pattern: null, invalid: true });
});

test("matches are marked case-insensitively, regex groups included", () => {
  const { pattern } = searchPattern("(time)d? ?out", true);

  const parts = highlightParts("upstream Timed out twice", pattern);

  assert.deepEqual(parts, [
    { text: "upstream ", hit: false },
    { text: "Timed out", hit: true },
    { text: " twice", hit: false },
  ]);
});

test("a pattern matching the empty string marks nothing and does not loop", () => {
  const { pattern } = searchPattern("x*", true);

  assert.deepEqual(highlightParts("abc", pattern), [{ text: "abc", hit: false }]);
});

test("matches gives the same answer when called twice with a global pattern", () => {
  const { pattern } = searchPattern("err", false);

  assert.equal(matches("err", pattern) && matches("err", pattern), true);
});

test("a JSON line gives its fields, nested keys dotted", () => {
  const fields = parseFields('{"msg":"hi","http":{"status":504},"tags":["a"]}');

  assert.deepEqual(fields, [
    ["msg", "hi"],
    ["http.status", "504"],
    ["tags", '["a"]'],
  ]);
});

test("a logfmt line gives its key=value pairs, quotes removed", () => {
  const fields = parseFields('level=warn msg="slow query" duration=1.2s');

  assert.deepEqual(fields, [
    ["level", "warn"],
    ["msg", "slow query"],
    ["duration", "1.2s"],
  ]);
});

test("a plain text line has no fields", () => {
  assert.equal(parseFields("upstream timed out (110) while reading"), null);
});

test("a logs link keeps its filters through the query string", () => {
  const query = { period: "6h", level: "error", q: "time.*out", regex: true, pod: "web-1" };

  const back = logsQueryFromParams(logsQueryToParams(query));

  assert.deepEqual(
    Object.fromEntries(Object.entries(back).filter(([, v]) => v !== undefined)),
    query,
  );
});

test("unknown levels and periods in a link are ignored", () => {
  const back = logsQueryFromParams(new URLSearchParams("level=loud&period=3y"));

  assert.equal(back.level ?? back.period, undefined);
});
