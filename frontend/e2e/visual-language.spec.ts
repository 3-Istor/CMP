import { test } from "@playwright/test";
import me from "./fixtures/me.json";
import apps from "./fixtures/project-apps.json";
import { installMockApi, VARIANTS } from "./fixtures/mock-api";

// Screenshots of the cluster visual language, light and dark; only with CNP_SHOTS_DIR set.
const DIR = process.env.CNP_SHOTS_DIR;
const admin = { "/account/me": { json: { ...me, cnp_admin: true } } };
const three = ["web", "api", "db"].map((n, i) => ({ ...apps[0], id: i + 1, name: `demo-${n}`, template_id: n === "db" ? "postgres" : n }));
const NAMES = ["prepare", "stop_source", "final_backup", "switch", "wait_target", "persist_restores", "cleanup_source", "finish"];
const running = {
  id: 7, source: "aws", target: "gcp", status: "running", detail: null, actor: "hugo", created_at: "2026-11-23T10:00:00Z", finished_at: null,
  steps: NAMES.map((name, i) => ({ name, status: i < 2 ? "done" : i === 2 ? "running" : "pending", detail: null, started_at: null, finished_at: null })),
};
const emergency = {
  clusters: [{ cloud: "onprem", state: "up" }, { cloud: "aws", state: "degraded" }, { cloud: "gcp", state: "down" }],
  projects: [
    { name: "shop", cloud: "gcp", cloud_state: "down", apps: ["web", "api", "db"], favorite_apps: ["api"], footprint: [{ project: "shop", cloud: "gcp", app: "web", cpu: 0.1, memory_bytes: 134217728, pods: 2, measured_at: "2026-11-23T14:32:00Z" }, { project: "shop", cloud: "gcp", app: "api", cpu: 0.35, memory_bytes: 536870912, pods: 2, measured_at: "2026-11-23T14:32:00Z" }, { project: "shop", cloud: "gcp", app: "db", cpu: 0.15, memory_bytes: 1610612736, pods: 2, measured_at: "2026-11-23T14:32:00Z" }], protected: true, favorite: true },
    { name: "blog", cloud: "gcp", cloud_state: "down", apps: ["site"], favorite_apps: [], footprint: [{ project: "blog", cloud: "gcp", app: "site", cpu: 0.1, memory_bytes: 134217728, pods: 2, measured_at: "2026-11-23T14:32:00Z" }], protected: false, favorite: false },
    { name: "iot", cloud: "aws", cloud_state: "degraded", apps: ["landing", "optispace", "db"], favorite_apps: [], footprint: [], protected: true, favorite: false },
    { name: "todo-move", cloud: "aws", cloud_state: "degraded", apps: ["frontend", "backend"], favorite_apps: [], footprint: [], protected: false, favorite: true },
  ],
  moves: [{ id: 3, project: "todo-move", source: "aws", target: "onprem", step: "final_backup", status: "running", started_at: "2026-11-23T14:00:00Z", auto: false }],
};
const placementCluster = (cloud: string, state: string, projects: object[], pct: number | null) => ({
  cloud, state, console_url: "https://c.example", nodes: 2, memory_requested_pct: pct, projects,
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(scheme, () => {
    test.skip(!DIR, "set CNP_SHOTS_DIR");
    test.use({ colorScheme: scheme, viewport: { width: 1360, height: 900 } });

    const shot = (page: import("@playwright/test").Page, name: string, full = true) =>
      page.screenshot({ path: `${DIR}/${name}-${scheme}.png`, fullPage: full });

    test("overview and settings", async ({ page }) => {
      await installMockApi(page, { ...VARIANTS.aws, ...admin, "/projects/demo/moves/footprint": { json: [{ project: "demo", cloud: "aws", app: "demo-web", cpu: 0.1, memory_bytes: 134217728, pods: 2, measured_at: "2026-11-23T14:32:00Z" }, { project: "demo", cloud: "aws", app: "demo-api", cpu: 0.35, memory_bytes: 536870912, pods: 2, measured_at: "2026-11-23T14:32:00Z" }, { project: "demo", cloud: "aws", app: "demo-db", cpu: 0.15, memory_bytes: 1610612736, pods: 2, measured_at: "2026-11-23T14:32:00Z" }] }, "/projects/demo/apps": { json: three }, "/projects/demo/moves/current": { json: null }, "/projects/demo/moves/data-loss": { json: [] },
        "/placement": { json: { clusters: [placementCluster("aws", "up", [], 64)] } } });
      await page.goto("/projects/demo");
      await page.getByText("Total 0.60 CPU").waitFor();
      await shot(page, "overview-card", false);
      await page.getByRole("button", { name: "Move", exact: true }).click();
      await page.getByRole("dialog").waitFor();
      await page.waitForTimeout(500);
      await shot(page, "overview-popover", false);
      await page.keyboard.press("Escape");
      await page.goto("/projects/demo/settings");
      await page.getByText("Runs on AWS").waitFor();
      await shot(page, "settings-location");
      await page.getByRole("button", { name: "Move to GCP" }).click();
      await page.getByRole("dialog").waitFor();
      await page.waitForTimeout(400);
      await shot(page, "settings-move-dialog", false);
    });

    test("overview during a move", async ({ page }) => {
      await installMockApi(page, { ...VARIANTS.aws, ...admin, "/projects/demo/apps": { json: three }, "/projects/demo/moves/current": { json: running } });
      await page.goto("/projects/demo");
      await page.getByText("Moving to GCP").first().waitFor();
      await shot(page, "overview-moving", false);
    });

    test("placement", async ({ page }) => {
      await installMockApi(page, { ...VARIANTS.aws, ...admin, "/placement": { json: { clusters: [
        placementCluster("onprem", "up", [{ name: "epitweet", apps: 3, moving_to: null, moving_from: null }, { name: "todo-move", apps: 2, moving_to: "aws", moving_from: null }], 58),
        placementCluster("aws", "degraded", [{ name: "iot", apps: 3, moving_to: null, moving_from: null }], 72),
        placementCluster("gcp", "up", [], 40),
      ] } } });
      await page.goto("/placement");
      await page.getByText("epitweet").waitFor();
      await page.waitForTimeout(1200);
      await shot(page, "placement");
    });

    test("emergency", async ({ page }) => {
      await installMockApi(page, { ...VARIANTS.aws, ...admin, "/emergency": { json: emergency },
        "/placement": { json: { clusters: [placementCluster("onprem", "up", [], 58), placementCluster("aws", "degraded", [], 72), placementCluster("gcp", "down", [], null)] } },
        "/projects/blog/moves/data-loss": { json: [] },
        "/projects/blog/moves/last-wal": { json: [{ database: "blog-db", last_wal_at: "2026-11-23T13:58:00Z", reason: null }] },
        "/projects/iot/moves/data-loss": { json: [] } });
      await page.goto("/emergency");
      await page.getByRole("heading", { name: "Act now" }).waitFor();
      await page.waitForTimeout(1200);
      await shot(page, "emergency");
      await page.getByRole("article", { name: "AWS" }).getByRole("button", { name: "Move" }).first().click();
      await page.getByRole("dialog").waitFor();
      await page.waitForTimeout(400);
      await shot(page, "emergency-popover", false);
      await page.keyboard.press("Escape");
      await page.getByRole("article", { name: "GCP" }).getByRole("button", { name: "Move" }).last().click();
      await page.getByRole("button", { name: /On-prem/ }).click();
      await page.getByLabel(/Type/).waitFor();
      await page.waitForTimeout(400);
      await shot(page, "emergency-source-down-confirm", false);
    });
  });
}
