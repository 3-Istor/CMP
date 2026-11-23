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
    { name: "shop", cloud: "gcp", cloud_state: "down", apps: ["web", "api"], favorite_apps: ["api"], protected: true, favorite: true },
    { name: "blog", cloud: "gcp", cloud_state: "down", apps: ["site"], favorite_apps: [], protected: false, favorite: false },
    { name: "iot", cloud: "aws", cloud_state: "degraded", apps: [], favorite_apps: [], protected: false, favorite: false },
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
  await expect(page.getByText("Priority", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: process.env.EMERGENCY_SHOT ?? "e2e/report/emergency.png", fullPage: true });
});
