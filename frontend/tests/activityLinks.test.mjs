import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const dependency = createRequire(new URL("../package.json", import.meta.url));
const ts = dependency("typescript");

// labels.ts only imports types, which the transpiler erases.
const source = readFileSync(new URL("../src/components/activity/labels.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const labels = {};
vm.runInThisContext(`(function (exports, require) {${outputText}\n})`)(labels, () => ({}));

const event = (action, fields = {}) => ({
  action,
  project: "demo",
  app: null,
  app_id: null,
  target: "",
  details: {},
  ...fields,
});

test("an event opens the page of the object it concerns", () => {
  const cases = [
    [event("security.exception.request", { app: "shop", app_id: 3 }), "/projects/demo/apps/3/security?tab=ignored"],
    [event("security.exception.approve"), "/projects/demo/security?tab=ignored"],
    [event("finops.recommendation.apply", { app: "shop", app_id: 3 }), "/projects/demo/apps/3/costs"],
    [event("finops.recommendation.ignore"), "/finops/recommendations?project=demo"],
    [event("access_review.start", { project: null, details: { campaign_id: 7 } }), "/access-reviews?campaign=7"],
    [event("access_review.close", { project: null, target: "campaign:8" }), "/access-reviews?campaign=8"],
    [event("access_review.decide", { target: "bob" }), "/access-reviews"],
    [event("access_review.start", { details: { campaign_id: "1&x=2" } }), "/access-reviews?campaign=1%26x%3D2"],
    [event("access.request", { project: null }), "/access"],
    [event("deployment.sync", { app: "shop", app_id: 3 }), "/projects/demo/apps/3/delivery"],
    [event("app.config.update", { app: "shop", app_id: 3 }), "/projects/demo/apps/3/settings"],
    [event("app.sleep", { app: "shop", app_id: 3 }), "/projects/demo/apps/3"],
    [event("project.member.add"), "/projects/demo/members"],
    [event("project.create"), "/projects/demo"],
    [event("finops.budget.update"), "/finops/budgets?project=demo"],
  ];
  for (const [e, href] of cases) assert.equal(labels.objectHref(e), href, e.action);
});

test("an event whose object is gone or has no page opens nothing", () => {
  assert.equal(labels.objectHref(event("app.delete", { app: "shop" })), null);
  assert.equal(labels.objectHref(event("project.delete")), null);
  assert.equal(labels.objectHref(event("keycloak.login", { project: null })), null);
  // App deleted since: no app id, and app events have no project page.
  assert.equal(labels.objectHref(event("app.sleep", { app: "gone" })), null);
});
