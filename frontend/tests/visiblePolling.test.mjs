import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { beforeEach, mock, test } from "node:test";
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

const { pollWhileVisible } = load("src/lib/visiblePolling.ts");

class FakeDocument extends EventTarget {
  visibilityState = "visible";
  show(state) {
    this.visibilityState = state;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

let doc;
beforeEach(() => {
  doc = new FakeDocument();
  globalThis.document = doc;
  mock.timers.reset();
  mock.timers.enable({ apis: ["setInterval"] });
});

test("polls on every interval while the tab is visible", () => {
  const tick = mock.fn();
  pollWhileVisible(tick, 1000);

  mock.timers.tick(3000);

  assert.equal(tick.mock.callCount(), 3);
});

test("does not poll while the tab is hidden", () => {
  const tick = mock.fn();
  pollWhileVisible(tick, 1000);

  doc.show("hidden");
  mock.timers.tick(10_000);

  assert.equal(tick.mock.callCount(), 0);
});

test("does not poll when started in a hidden tab", () => {
  doc.visibilityState = "hidden";
  const tick = mock.fn();

  pollWhileVisible(tick, 1000);
  mock.timers.tick(10_000);

  assert.equal(tick.mock.callCount(), 0);
});

test("refreshes once as soon as the tab shows again, then resumes the interval", () => {
  const tick = mock.fn();
  pollWhileVisible(tick, 1000);
  doc.show("hidden");
  mock.timers.tick(10_000);

  doc.show("visible");
  const onShow = tick.mock.callCount();
  mock.timers.tick(1000);

  assert.deepEqual([onShow, tick.mock.callCount()], [1, 2]);
});

test("a repeated visible event does not refresh twice", () => {
  const tick = mock.fn();
  pollWhileVisible(tick, 1000);

  doc.show("visible");

  assert.equal(tick.mock.callCount(), 0);
});

test("stops polling and listening once stopped", () => {
  const tick = mock.fn();
  const stop = pollWhileVisible(tick, 1000);

  stop();
  doc.show("hidden");
  doc.show("visible");
  mock.timers.tick(10_000);

  assert.equal(tick.mock.callCount(), 0);
});
