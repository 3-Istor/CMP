import { expect, test } from "@playwright/test";
import { installMockApi, VARIANTS } from "./fixtures/mock-api";

const A = {
  time: "2026-11-17T10:00:00Z",
  message: "first line from the store",
  pod: "demo-app-7c9d8f6b54-x2k9p",
  container: "demo-app",
  level: "info",
  trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
};
const B = { ...A, time: "2026-11-17T10:00:01Z", message: "second line from the store", trace_id: null };

test("follows the observability store live", async ({ page }) => {
  await installMockApi(page, VARIANTS.obslogs);
  // The live poll re-reads a window that still holds A: A must not come back twice.
  let following = false;
  await page.route("**/api/deployments/1/observability/logs?**", (route) =>
    route.fulfill({ json: following ? [B, A] : [A] }),
  );
  await page.goto("/projects/demo/apps/1/logs");

  await expect(page.getByText(A.message)).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open the trace" }).and(page.locator(`[href$="trace=${A.trace_id}"]`)),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Pod" })).toHaveCount(0);

  following = true;
  await page.getByRole("button", { name: "Follow" }).click();
  await expect(page.getByText(B.message)).toHaveCount(1, { timeout: 10_000 });
  await expect(page.getByText("2 lines", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(A.message)).toHaveCount(1);
});

test("reads the activity logs without the store", async ({ page }) => {
  // The default mock answers 404 on the capabilities request.
  await installMockApi(page);
  await page.goto("/projects/demo/apps/1/logs");

  await expect(page.getByText("server listening on")).toBeVisible();
  const level = page.getByRole("combobox", { name: "Level" });
  await expect(level).toBeVisible();
  await expect(level.getByRole("option", { name: "Info" })).toHaveCount(0);
});
