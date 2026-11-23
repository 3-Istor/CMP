import { test, expect } from "@playwright/test";
import me from "./fixtures/me.json";
import { installMockApi, VARIANTS } from "./fixtures/mock-api";

const emergency = {
  clusters: [
    { cloud: "onprem", state: "up" },
    { cloud: "aws", state: "degraded" },
    { cloud: "gcp", state: "down" },
  ],
  projects: [
    { name: "shop", cloud: "gcp", cloud_state: "down", apps: ["web", "api"], favorite_apps: ["api"], footprint: [{ project: "shop", cloud: "gcp", app: "web", cpu: 0.1, memory_bytes: 134217728, pods: 2, measured_at: "2026-11-23T14:32:00Z" }, { project: "shop", cloud: "gcp", app: "api", cpu: 0.35, memory_bytes: 536870912, pods: 2, measured_at: "2026-11-23T14:32:00Z" }], protected: true, favorite: true },
    { name: "blog", cloud: "gcp", cloud_state: "down", apps: ["site"], favorite_apps: [], footprint: [], protected: false, favorite: false },
    { name: "iot", cloud: "aws", cloud_state: "degraded", apps: [], favorite_apps: [], footprint: [], protected: false, favorite: false },
  ],
  moves: [
    { id: 2, project: "shop", source: "gcp", target: "aws", step: "switch", status: "failed", started_at: "2026-11-23T10:00:00Z", auto: true },
    { id: 1, project: "blog", source: "aws", target: "gcp", step: "finish", status: "done", started_at: "2026-11-22T10:00:00Z", auto: false },
  ],
};

test("platform admin sees the outage view", async ({ page }) => {
  await installMockApi(page, {
    ...VARIANTS.aws,
    "/account/me": { json: { ...me, cnp_admin: true } },
    "/emergency": { json: emergency },
    "/infra/health": { json: { clusters: [{ cloud: "aws", state: "up" }, { cloud: "gcp", state: "up" }, { cloud: "onprem", state: "up" }] } },
  });
  if (process.env.PLACEMENT_DARK) await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/emergency");
  await expect(page.getByRole("heading", { name: "Act now" })).toBeVisible();
  await expect(page.getByRole("link", { name: "shop" }).first()).toBeVisible();
  await expect(page.getByRole("img", { name: "Favorite" }).first()).toBeVisible();
  await page.screenshot({ path: process.env.EMERGENCY_SHOT ?? "e2e/report/emergency.png", fullPage: true });
});

test("an admin moves a project off a down cluster in two clicks plus the typed name", async ({ page }) => {
  const posts: unknown[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/projects/shop/move")) posts.push(r.postDataJSON());
  });
  await installMockApi(page, {
    ...VARIANTS.aws,
    "/account/me": { json: { ...me, cnp_admin: true } },
    "/emergency": { json: { ...emergency, moves: [] } },
    "/projects/shop/moves/data-loss": { json: [] },
    "/projects/shop/moves/last-wal": { json: [{ database: "shop-db", last_wal_at: "2026-11-23T14:02:00Z", reason: null }] },
    "/projects/shop/move": { status: 202, json: { id: 9, source: "gcp", target: "onprem", status: "running", detail: null, actor: "hugo", created_at: "2026-11-23T14:10:00Z", finished_at: null, steps: [] } },
  });
  if (process.env.PLACEMENT_DARK) await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/emergency");
  await page.getByRole("article", { name: "GCP" }).getByRole("button", { name: "Move" }).first().click();
  // AWS is degraded: not a valid target.
  await expect(page.getByRole("button", { name: /AWS/ })).toHaveAttribute("aria-disabled", "true");
  await page.getByRole("button", { name: /On-prem/ }).click();
  await expect(page.getByText(/Data written after .* will be lost \(shop-db\)/)).toBeVisible();
  const confirm = page.getByRole("dialog").getByRole("button", { name: "Move", exact: true });
  await expect(confirm).toBeDisabled();
  await page.getByLabel(/Type/).fill("shop");
  await confirm.click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ confirm_data_loss: true, target: "onprem" });
});

test("the move popover closes with Escape", async ({ page }) => {
  await installMockApi(page, {
    ...VARIANTS.aws,
    "/account/me": { json: { ...me, cnp_admin: true } },
    "/emergency": { json: emergency },
  });
  await page.goto("/emergency");
  // shop has a failed move: blog is the row with a Move button.
  await page.getByRole("article", { name: "GCP" }).getByRole("button", { name: "Move" }).click();
  await expect(page.getByRole("dialog", { name: "Move blog" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Move blog" })).toHaveCount(0);
});

test("a down cluster shows each app's last footprint and the project total", async ({ page }) => {
  await installMockApi(page, {
    ...VARIANTS.aws,
    "/account/me": { json: { ...me, cnp_admin: true } },
    "/emergency": { json: emergency },
  });
  await page.goto("/emergency");
  await expect(page.getByText("0.35 CPU · 512 Mi")).toBeVisible();
  await expect(page.getByText(/Total 0\.45 CPU · 640 Mi · last seen \d\d:\d\d/)).toBeVisible();
});
