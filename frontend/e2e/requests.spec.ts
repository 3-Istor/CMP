import { test, expect, type Page } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

// Request-count regressions with the mock API. The page clock is paused after
// load, so runFor() is the only time source and the counts are exact.
const MINUTE_MS = 60_000;

function track(page: Page) {
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

async function load(page: Page, path: string) {
  await page.clock.install();
  await installMockApi(page, {});
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await page.clock.pauseAt((await page.evaluate(() => Date.now())) + 1_000);
}

// Step a minute in 5 s slices, letting each poll's answer land before the next one.
async function minute(page: Page) {
  for (let i = 0; i < MINUTE_MS / 5_000; i++) {
    await page.clock.runFor(5_000);
    await page.waitForTimeout(200);
  }
}

test("home asks one batch health call and no per-app health", async ({ page }) => {
  const paths = track(page);
  await load(page, "/");
  expect(count(paths, "/infra/deployments/health")).toBe(1);
  expect(paths.filter((p) => /^\/infra\/deployments\/\d+\/health$/.test(p))).toEqual([]);
  expect(count(paths, "/finops/overview")).toBe(0);
});

test("sidebar and home share polled reads over a minute", async ({ page }) => {
  const paths = track(page);
  await load(page, "/finops");
  await page.clock.runFor(10_000);
  await page.waitForTimeout(500);
  paths.length = 0;
  await page.getByRole("link", { name: "Home", exact: true }).first().click();
  await page.waitForURL("**/");
  await page.waitForLoadState("networkidle");
  await minute(page);
  // Before sharing: 5, 5 and 1 (the home's own timers ran next to the sidebar's).
  expect(count(paths, "/deployments/")).toBe(2);
  expect(count(paths, "/projects/")).toBe(4);
  expect(count(paths, "/security/overview")).toBe(0);
});

test("a lone poller keeps its own cadence", async ({ page }) => {
  const paths = track(page);
  await load(page, "/projects/demo/apps/1");
  paths.length = 0;
  await minute(page);
  // ComponentsCard polls health every 10 s; the 15 s header poll reuses its answers.
  expect(count(paths, "/infra/deployments/1/health")).toBe(6);
});
