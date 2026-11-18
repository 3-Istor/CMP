import { test, expect, type Page } from "@playwright/test";
import { installMockApi, VARIANTS } from "./fixtures/mock-api";

const SHOTS = process.env.MOVE_SHOTS_DIR;
const shot = (page: Page, name: string) =>
  SHOTS ? page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true }) : Promise.resolve();

const NAMES = ["prepare", "stop_source", "final_backup", "switch", "wait_target", "persist_restores", "cleanup_source", "finish"];

function move(status: "running" | "failed", done: number) {
  return {
    id: 7,
    source: "aws",
    target: "gcp",
    status,
    detail: status === "failed" ? "wait_target: timed out" : null,
    actor: "hugo",
    created_at: "2026-11-18T10:00:00Z",
    finished_at: null,
    steps: NAMES.map((name, i) => ({
      name,
      status: i < done ? "done" : i === done ? (status === "failed" ? "failed" : "running") : "pending",
      detail: null,
      started_at: i <= done ? "2026-11-18T10:00:00Z" : null,
      finished_at: i < done ? "2026-11-18T10:01:00Z" : null,
    })),
  };
}

const accepted = { status: 202, json: move("running", 0) };

test("admin moves the project once, with the name typed", async ({ page }) => {
  const posts: unknown[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/projects/demo/move")) posts.push(r.postDataJSON());
  });
  await installMockApi(page, { ...VARIANTS.aws, "/projects/demo/moves/current": { json: null }, "/projects/demo/move": accepted });
  await page.goto("/projects/demo/settings");
  await expect(page.getByText("Runs on AWS")).toBeVisible();
  await shot(page, "1-admin-button");
  await page.getByRole("button", { name: "Move to GCP" }).click();
  await expect(page.getByText("Apps stop until the project runs on GCP.")).toBeVisible();
  await page.getByLabel(/Type/).fill("demo");
  await shot(page, "2-confirm-dialog");
  await page.getByRole("button", { name: "Move", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ confirm_data_loss: false });
});

test("backing apps need the data loss box", async ({ page }) => {
  let body: unknown;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/projects/demo/move")) body = r.postDataJSON();
  });
  await installMockApi(page, { ...VARIANTS.aws, ...VARIANTS.imported, "/projects/demo/moves/current": { json: null }, "/projects/demo/move": accepted });
  await page.goto("/projects/demo/settings");
  await page.getByRole("button", { name: "Move to GCP" }).click();
  await page.getByLabel("Data in demo-app will be lost").check();
  await page.getByLabel(/Type/).fill("demo");
  await page.getByRole("button", { name: "Move", exact: true }).click();
  await expect.poll(() => body).toEqual({ confirm_data_loss: true });
});

test("a member has no move button", async ({ page }) => {
  await installMockApi(page, { ...VARIANTS.aws, ...VARIANTS.member });
  await page.goto("/projects/demo/settings");
  await expect(page.getByRole("heading", { name: "Project settings" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Move to/ })).toHaveCount(0);
});

test("the button is disabled while the other cloud is down", async ({ page }) => {
  const health = { openstack_hypervisors: [], clusters: [{ cloud: "aws", state: "up" }, { cloud: "gcp", state: "down" }] };
  await installMockApi(page, { ...VARIANTS.aws, "/infra/health": { json: health }, "/projects/demo/moves/current": { json: null } });
  await page.goto("/projects/demo/settings");
  await expect(page.getByRole("button", { name: "Move to GCP" })).toBeDisabled();
  await expect(page.getByText("GCP is not up.")).toBeVisible();
});

test("an active move shows the banner and the steps", async ({ page }) => {
  await installMockApi(page, { ...VARIANTS.aws, "/projects/demo/moves/current": { json: move("running", 2) } });
  await page.goto("/projects/demo");
  await expect(page.getByText("Moving to GCP · Final backup")).toBeVisible();
  await shot(page, "3-banner");
  await page.getByRole("link", { name: "Details" }).click();
  await expect(page.getByText("Wait for apps")).toBeVisible();
  await expect(page.getByRole("button", { name: "Move to GCP" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cancel move" })).toHaveCount(0);
  await shot(page, "4-steps");
});

test("cancel is offered until the source is stopped", async ({ page }) => {
  await installMockApi(page, { ...VARIANTS.aws, "/projects/demo/moves/current": { json: move("running", 1) } });
  await page.goto("/projects/demo/settings");
  await expect(page.getByRole("button", { name: "Cancel move" })).toBeVisible();
});

test("a failed move offers Retry", async ({ page }) => {
  await installMockApi(page, { ...VARIANTS.aws, "/projects/demo/moves/current": { json: move("failed", 4) } });
  await page.goto("/projects/demo/settings");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.getByText("wait_target: timed out")).toBeVisible();
  await shot(page, "5-failed");
});

test("an on-prem project cannot move", async ({ page }) => {
  await installMockApi(page, { "/projects/demo/moves/current": { json: null } });
  await page.goto("/projects/demo/settings");
  await expect(page.getByText("Moving on-prem projects is not available yet.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Move to/ })).toHaveCount(0);
  await shot(page, "6-onprem");
});
