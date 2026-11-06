import type { Page } from "@playwright/test";
import appHealth from "./app-health.json";
import finopsOverview from "./finops-overview.json";
import infraHealth from "./infra-health.json";
import me from "./me.json";
import platformStatus from "./platform-status.json";
import projectApps from "./project-apps.json";
import projectLinks from "./project-links.json";
import projects from "./projects.json";
import session from "./session.json";

const ROUTES: Record<string, unknown> = {
  "/account/me": me,
  "/projects/": projects,
  "/projects/demo/apps": projectApps,
  "/projects/demo/links": projectLinks,
  "/deployments/": projectApps,
  "/deployments/1": projectApps[0],
  "/infra/health": infraHealth,
  // Health badges only render with a health answer; without it axe never checks their colours.
  "/infra/deployments/1/health": appHealth,
  "/activity/platform": platformStatus,
  "/finops/overview": finopsOverview,
};

export const unmockedRequests = new Set<string>();

export async function installMockApi(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === "/api/auth/session") {
      await route.fulfill({ json: session });
      return;
    }

    const apiPath = url.pathname.replace(/^\/api/, "");
    if (!(apiPath in ROUTES)) {
      unmockedRequests.add(`${request.method()} ${apiPath}`);
      await route.fulfill({ status: 404, json: { detail: "not mocked in e2e fixtures" } });
      return;
    }

    await route.fulfill({ json: ROUTES[apiPath] });
  });
}
