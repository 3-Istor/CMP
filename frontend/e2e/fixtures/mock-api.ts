import type { Page } from "@playwright/test";
import access from "./access.json";
import agentsSummary from "./agents-summary.json";
import accessReview1 from "./access-review-1.json";
import accessReviewMine from "./access-review-mine.json";
import accessReviews from "./access-reviews.json";
import appHealth from "./app-health.json";
import finopsMeta from "./finops-meta.json";
import finopsOverview from "./finops-overview.json";
import githubStatus from "./github-status.json";
import importRepos from "./import-repos.json";
import importRun from "./import-run.json";
import imports from "./imports.json";
import infraHealth from "./infra-health.json";
import me from "./me.json";
import platformStatus from "./platform-status.json";
import preferences from "./preferences.json";
import projectApps from "./project-apps.json";
import projectLinks from "./project-links.json";
import appLinks from "./app-links.json";
import projects from "./projects.json";
import scorecard from "./scorecard.json";
import security from "./security.json";
import session from "./session.json";
import topology from "./topology.json";

const ROUTES: Record<string, unknown> = {
  "/account/me": me,
  "/access": access,
  "/account/preferences": preferences,
  "/agents/summary": agentsSummary,
  "/access-reviews": accessReviews,
  "/access-reviews/campaigns/1": accessReview1,
  "/access-reviews/mine": accessReviewMine,
  "/projects/": projects,
  "/projects/demo/apps": projectApps,
  "/projects/demo/links": projectLinks,
  "/deployments/1/links": appLinks,
  "/deployments/": projectApps,
  "/deployments/1": projectApps[0],
  "/infra/health": infraHealth,
  // Health badges only render with a health answer; without it axe never checks their colours.
  "/infra/deployments/1/health": appHealth,
  "/activity/platform": platformStatus,
  "/finops/overview": finopsOverview,
  "/finops/meta": finopsMeta,
  "/deployments/1/scorecard": scorecard,
  "/security/summary": security.summary,
  "/security/findings": security.findings,
  "/security/trend": security.trend,
  "/security/exceptions": security.exceptions,
  "/security/me": security.me,
  "/account/github": githubStatus,
  "/projects/demo/topology": topology,
  "/projects/demo/imports": imports,
  "/projects/demo/import/repos": importRepos,
  "/projects/demo/imports/runs/r1": importRun,
};

export const unmockedRequests = new Set<string>();

type Override = { status?: number; json: unknown };

const app = projectApps[0];
const publicApps = [
  app,
  {
    ...app,
    id: 2,
    name: "shop-api",
    terraform_outputs: '{"app_url":"https://shop-api-demo.3istor.com","hostname":"shop-api-demo.3istor.com"}',
    staging_url: null,
  },
];
const notConnected: Override = { status: 503, json: { detail: "cluster_not_connected", cloud: "aws" } };

// Named answers layered over ROUTES. A key ending in "*" is a prefix; exact keys win.
export const VARIANTS: Record<string, Record<string, Override>> = {
  publicapps: {
    "/projects/demo/apps": { json: publicApps },
    "/deployments/": { json: publicApps },
  },
  deleting: {
    "/projects/demo/apps": { json: [{ ...app, status: "deleting", step_message: "Destroying resources" }] },
    "/deployments/": { json: [{ ...app, status: "deleting", step_message: "Destroying resources" }] },
    "/deployments/1": { json: { ...app, status: "deleting", step_message: "Destroying resources" } },
  },
  imported: {
    "/projects/demo/apps": { json: [{ ...app, template_id: "import-service" }] },
    "/deployments/1": { json: { ...app, template_id: "import-service" } },
    "/projects/demo/imports": {
      json: {
        imports: [
          {
            ...imports.imports[0],
            services: [{ app: "demo-app", kind: "service", path: ".", status: "running", message: null, last_sha: null, last_digest: null, deployment_id: 1, public: false, warnings: [] }],
          },
        ],
      },
    },
  },
  importmissing: {
    "/projects/demo/apps": { json: [{ ...app, template_id: "import-service" }] },
    "/deployments/1": { json: { ...app, template_id: "import-service" } },
  },
  protectedimported: {
    "/projects/demo/apps": { json: [{ ...app, template_id: "import-service" }] },
    "/deployments/1": { json: { ...app, template_id: "import-service" } },
    "/projects/demo/deletion-protection": { json: { enabled: true } },
  },
  protected: { "/projects/demo/deletion-protection": { json: { enabled: true } } },
  member: { "/projects/": { json: [{ ...projects[0], role: "member" }] } },
  notconnected: {
    "/deployments/1/*": notConnected,
    "/deployments/1/observability": {
      json: { metrics: true, logs: true, traces: true, alerts: true, slo: true, silence_max_hours: 24, grafana_url: "" },
    },
  },
};

export async function installMockApi(page: Page, overrides: Record<string, Override> = {}): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === "/api/auth/session") {
      await route.fulfill({ json: session });
      return;
    }

    const apiPath = url.pathname.replace(/^\/api/, "");
    const override =
      overrides[apiPath] ??
      Object.entries(overrides).find(([k]) => k.endsWith("*") && apiPath.startsWith(k.slice(0, -1)) && !(apiPath in ROUTES))?.[1];
    if (override) {
      await route.fulfill({ status: override.status ?? 200, json: override.json });
      return;
    }
    if (!(apiPath in ROUTES)) {
      unmockedRequests.add(`${request.method()} ${apiPath}`);
      await route.fulfill({ status: 404, json: { detail: "not mocked in e2e fixtures" } });
      return;
    }

    await route.fulfill({ json: ROUTES[apiPath] });
  });
}
