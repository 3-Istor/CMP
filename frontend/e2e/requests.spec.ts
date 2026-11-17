import { test, expect } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

// Request-count regressions (mock API, faked page clock so cache ages follow the stepped time).
const STEP_MS = 5_000;
const STEPS = 12;

async function track(page: import("@playwright/test").Page) {
  const paths: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith("/api/") && !u.pathname.startsWith("/api/auth")) {
      paths.push(u.pathname.replace(/^\/api/, ""));
    }
  });
  return paths;
}

const count = (paths: string[], path: string) => paths.filter((p) => p === path).length;

test("home asks one batch health call and no per-app health", async ({ page }) => {
  await page.clock.install();
  await installMockApi(page, {});
  const paths = await track(page);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(count(paths, "/infra/deployments/health")).toBe(1);
  expect(paths.filter((p) => /^\/infra\/deployments\/\d+\/health$/.test(p))).toEqual([]);
  expect(count(paths, "/finops/overview")).toBe(0);
});

test("sidebar and home share polled reads over a minute", async ({ page }) => {
  await page.clock.install();
  await installMockApi(page, {});
  const paths = await track(page);
  await page.goto("/finops");
  await page.waitForLoadState("networkidle");
  await page.clock.runFor(10_000);
  await page.waitForTimeout(500);
  paths.length = 0;
  await page.getByRole("link", { name: "Home", exact: true }).first().click();
  await page.waitForURL("**/");
  for (let i = 0; i < STEPS; i++) {
    await page.clock.runFor(STEP_MS);
    await page.waitForTimeout(250);
  }
  expect(count(paths, "/deployments/")).toBeLessThanOrEqual(2);
  expect(count(paths, "/projects/")).toBeLessThanOrEqual(4);
});
