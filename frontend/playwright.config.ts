import { defineConfig } from "@playwright/test";

const baseURL = "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  outputDir: "./e2e/report/test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: [["list"]],
  use: {
    baseURL,
    locale: "en-US",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "npx next dev --hostname localhost --port 3000",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      CMP_E2E_FAKE_SESSION: "1",
      AUTH_SECRET: "e2e-only-secret-never-used-outside-playwright",
      KEYCLOAK_ISSUER: "http://localhost:8080/realms/e2e",
      KEYCLOAK_CLIENT_ID: "e2e",
      KEYCLOAK_CLIENT_SECRET: "e2e",
    },
  },
});
