import { test, expect } from "@playwright/test";
import me from "./fixtures/me.json";
import { installMockApi, VARIANTS } from "./fixtures/mock-api";

const NAMES = ["prepare", "stop_source", "final_backup", "switch", "wait_target", "persist_restores", "cleanup_source", "finish"];
const pod = (name: string, kind: string, status = "Running") => ({ name, namespace: "demo-api", status, kind });

const placement = {
  clusters: [
    { cloud: "onprem", state: "up", console_url: "https://argocd.example/applications", nodes: [{ name: "node-2", instance_type: null, pods: [pod("demo-web-1", "web", "Pending")] }] },
    { cloud: "aws", state: "up", console_url: "https://aws.example/eks", nodes: [{ name: "ip-10-0-1-87", instance_type: "m7i-flex.large", pods: [pod("demo-api-1", "api")] }] },
    { cloud: "gcp", state: "unreachable", console_url: "https://gcp.example/gke", nodes: [] },
  ],
  database: { name: "demo-db-r4", generation: 4, last_archived_wal: null, archiving: true },
  backups: [{ claim: "demo-db-backup", bucket: "cnpg-demo-db", uid: "u1" }],
};

const move = {
  id: 7, source: "aws", target: "onprem", status: "running", detail: null, actor: "hugo",
  created_at: "2026-11-22T10:00:00Z", finished_at: null,
  steps: NAMES.map((name, i) => ({
    name, status: i < 2 ? "done" : i === 2 ? "running" : "pending", detail: null,
    started_at: i <= 2 ? "2026-11-22T10:00:00Z" : null, finished_at: i < 2 ? "2026-11-22T10:00:20Z" : null,
  })),
};

test("platform admin sees the live placement of a project", async ({ page }) => {
  await installMockApi(page, {
    ...VARIANTS.aws,
    "/account/me": { json: { ...me, cnp_admin: true } },
    "/projects/demo/moves/current": { json: move },
    "/projects/demo/placement": { json: placement },
  });
  await page.goto("/projects/demo");
  await expect(page.getByRole("heading", { name: "Placement" })).toBeVisible();
  await expect(page.getByText("demo-web-1")).toBeVisible();
  await expect(page.getByText("Unreachable")).toBeVisible();
  await expect(page.getByText("demo-db-backup")).toBeVisible();
  await expect(page.getByText("Archiving on")).toBeVisible();
  if (process.env.PLACEMENT_SHOT) {
    await page.getByText("Move events").scrollIntoViewIfNeeded();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: process.env.PLACEMENT_SHOT });
  }
});

test("a project admin who is not platform admin has no placement", async ({ page }) => {
  await installMockApi(page, { ...VARIANTS.aws, "/projects/demo/moves/current": { json: null } });
  await page.goto("/projects/demo");
  await expect(page.getByRole("heading", { name: "Placement" })).toHaveCount(0);
});
