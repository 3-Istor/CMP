import { test, expect, type Page } from "@playwright/test";
import { agents, sse, testRun } from "./fixtures/ai";
import { installMockApi } from "./fixtures/mock-api";
import me from "./fixtures/me.json";

const SHOTS = process.env.AI_SHOTS_DIR;
const shot = (page: Page, name: string) =>
  SHOTS ? page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true }) : Promise.resolve();

test.beforeEach(async ({ context }) => {
  await context.addCookies([{ name: "NEXT_LOCALE", value: "fr", url: "http://localhost:3000" }]);
});

test("the project IA tab gathers model access, hosted agents and connected assistants", async ({ page }) => {
  await installMockApi(page);

  await page.goto("/projects/demo/assistants");

  await expect(page).toHaveURL(/\/projects\/demo\/ai$/);
  await expect(page.getByRole("heading", { name: "Accès aux modèles" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agents hébergés" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Assistants connectés" })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Tokens consommés aujourd'hui" })).toHaveAttribute("aria-valuenow", "52300");
  await expect(page.getByRole("link", { name: /grok-reader/ })).toBeVisible();
  await expect(page.getByText("En ligne")).toBeVisible();
  await shot(page, "1-ia-tab");
});

test("a sentence drafts the agent, the map lights the risks and three risks block creation", async ({ page }) => {
  let created: unknown;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/projects/demo/agents")) created = r.postDataJSON();
  });
  await installMockApi(page, {
    "/projects/demo/agents": { json: { agents: [agents[0]], pull_request: { number: 12, url: "https://github.com/3-Istor/epitweet/pull/12" } } },
  });
  await page.goto("/projects/demo/ai/new");

  await page.getByLabel("Décrivez votre agent en une phrase").fill("Un bot qui répond aux mentions @grok en vérifiant les faits");
  await page.getByRole("button", { name: "Rédiger" }).click();
  await expect(page.getByRole("heading", { name: "Identité de l'agent" })).toBeFocused();
  await expect(page.getByLabel("Nom", { exact: true })).toHaveValue("grok");
  const map = page.getByRole("complementary", { name: "Carte des capacités" });
  await expect(map.getByRole("region", { name: "Capacités de grok-reader" })).toBeVisible();
  await expect(map.getByRole("region", { name: "Capacités de grok-actor" })).toBeVisible();
  await shot(page, "2-wizard-identity");

  await page.getByRole("button", { name: "Suivant" }).click();
  await page.getByRole("button", { name: "Suivant" }).click();
  await expect(page.getByRole("checkbox", { name: /run_python/ }).first()).toBeChecked();
  await shot(page, "3-wizard-tools");
  await page.getByRole("button", { name: "Suivant" }).click();

  await page.getByRole("switch", { name: /Accède-t-il à des données privées/ }).click();
  await expect(map.getByText("3 sur 3")).toBeVisible();
  await expect(page.getByRole("button", { name: "Suivant" })).toBeDisabled();
  await shot(page, "4-wizard-risk-blocked");
  await page.getByRole("switch", { name: /Accède-t-il à des données privées/ }).click();
  await page.getByRole("button", { name: "Suivant" }).click();

  await expect(page.getByText("agent-demo-grok-reader", { exact: true })).toBeVisible();
  await expect(page.getByText("demo-grok-actor", { exact: true })).toBeVisible();
  await shot(page, "5-wizard-summary");
  await page.getByRole("button", { name: "Créer les deux agents" }).click();

  await expect(page.getByRole("heading", { name: "Agent en cours de création" })).toBeFocused();
  await expect(page.getByRole("link", { name: "Voir la PR n°12" })).toBeVisible();
  expect(created).toMatchObject({
    template: "app-bot",
    name: "grok",
    repo_app: "demo-app",
    isolation: null,
    tools: ["epitweet-mcp__get_thread", "web_search", "run_python"],
    actor: { tools: ["epitweet-mcp__reply_as_grok"] },
  });
});

test("the app bot template starts a reader and actor chain from its catalogue defaults", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/projects/demo/ai/new");

  const card = page.getByRole("button", { name: /Bot d'app, lecteur et acteur/ });
  await expect(card.getByText("Exécute du code")).toBeVisible();
  await card.click();

  await page.getByLabel("Nom", { exact: true }).fill("grok");
  const map = page.getByRole("complementary", { name: "Carte des capacités" });
  await expect(map.getByRole("region", { name: "Capacités de grok-reader" })).toBeVisible();
  const actor = map.getByRole("region", { name: "Capacités de grok-actor" });
  await expect(actor.getByText("Sortie structurée de grok-reader")).toBeVisible();
  await expect(actor.getByText("epitweet-mcp__reply_as_grok")).toBeVisible();
});

test("the test chat streams the answer next to every step and its cost", async ({ page }) => {
  await installMockApi(page);
  await page.route("**/api/projects/demo/agents/grok-reader/test", (route) =>
    route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: sse(testRun) }),
  );
  await page.goto("/projects/demo/ai/agents/grok-reader/test");

  await page.getByLabel("Message à l'agent").fill("Le chômage est-il à 7,4 % ?");
  await page.keyboard.press("Enter");

  await expect(page.getByText("Le taux est de 7,4 %, selon l'INSEE.")).toBeVisible();
  const steps = page.getByRole("list", { name: /Étapes de la réponse/ });
  await expect(steps.getByText("Appel du modèle")).toHaveCount(2);
  await expect(steps.getByText("web_search")).toBeVisible();
  await expect(steps.getByLabel("Code Python")).toContainText("plt.savefig");
  await expect(steps.getByRole("img", { name: /chart\.png/ })).toBeVisible();
  await expect(steps.getByRole("alert")).toContainText("politique de la plateforme");
  await expect(page.getByText(/8,2\s?k entrée · 350 sortie/).first()).toBeVisible();
  await steps.getByRole("button", { name: "Arguments" }).first().click();
  await expect(steps.getByText("taux de chômage France 2026")).toBeVisible();
  await shot(page, "6-agent-test");
});

test("the kill switch suspends the agent in one click and says so", async ({ page }) => {
  let suspended = false;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/grok-reader/suspend")) suspended = true;
  });
  await installMockApi(page);
  await page.goto("/projects/demo/ai/agents/grok-reader");

  await page.getByRole("button", { name: "Couper l'agent" }).click();

  await expect.poll(() => suspended).toBe(true);
  await expect(page.getByRole("button", { name: "Réactiver" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/Agent suspendu : son client Keycloak est désactivé/)).toBeVisible();
  await shot(page, "7-agent-suspended");
});

test("platform admins see every agent, the MCP servers, the models and the global budget", async ({ page }) => {
  await installMockApi(page, { "/account/me": { json: { ...me, cnp_admin: true } } });
  await page.goto("/agents");

  await expect(page.getByRole("meter", { name: "Budget IA de la plateforme" })).toHaveAttribute("aria-valuetext", /0,42/);
  await page.getByRole("tab", { name: "Tous les agents" }).click();
  await expect(page.getByRole("row")).toHaveCount(agents.length + 1);
  await expect(page.getByRole("button", { name: "Réactiver diag-deploiement" })).toBeVisible();
  await shot(page, "8-agents-admin");
  await page.getByRole("tab", { name: "Outils MCP" }).click();
  await expect(page.getByText("epitweet-mcp__reply_as_grok")).toBeVisible();
});

test("a member sees only the agents of the projects they belong to", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/agents");

  await expect(page.getByRole("link", { name: /grok-actor/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Tous les agents" })).toHaveCount(0);
});

test("an app with model access carries the Accès IA badge", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/projects/demo/apps/1");

  await expect(page.getByRole("link", { name: "Accès IA" })).toHaveAttribute("href", "/projects/demo/ai");
});

test("FinOps breaks the IA category down per agent and app", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/finops");

  const table = page.getByRole("table", { name: "Tokens et coût par agent ou app" });
  await expect(table.getByRole("link", { name: "grok-reader" })).toBeVisible();
  await expect(table.getByText("demo-app")).toBeVisible();
  await shot(page, "9-finops-ia");
});
