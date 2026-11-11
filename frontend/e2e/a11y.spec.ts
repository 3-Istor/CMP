import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { installMockApi, unmockedRequests } from "./fixtures/mock-api";

const PAGES = [
  "/",
  "/welcome",
  "/finops",
  "/security",
  "/activity",
  "/alerts",
  "/access",
  "/projects/demo",
  "/projects/demo/costs",
  "/projects/demo/apps/1",
  "/projects/demo/apps/1/logs",
  "/projects/demo/apps/1/delivery",
  "/projects/demo/apps/1/settings",
];
const THEMES = ["light", "dark"] as const;
const VIEWPORT_WIDTHS = [1280, 400];
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];
const REPORT_DIR = join(__dirname, "report");

type Theme = (typeof THEMES)[number];

interface ScanResult {
  page: string;
  theme: Theme;
  viewportWidth: number;
  violations: {
    id: string;
    impact: string | null | undefined;
    help: string;
    helpUrl: string;
    nodeCount: number;
    targets: string[];
  }[];
}

const scans: ScanResult[] = [];

test.afterAll(() => {
  mkdirSync(REPORT_DIR, { recursive: true });
  writeFileSync(
    join(REPORT_DIR, "a11y-report.json"),
    JSON.stringify(
      { generatedAt: new Date().toISOString(), unmockedRequests: [...unmockedRequests].sort(), scans },
      null,
      2,
    ),
  );
  writeFileSync(join(REPORT_DIR, "a11y-report.md"), renderMarkdown(scans, [...unmockedRequests].sort()));
});

for (const path of PAGES) {
  for (const theme of THEMES) {
    for (const viewportWidth of VIEWPORT_WIDTHS) {
      test(`${path} has no critical axe violations (${theme}, ${viewportWidth}px)`, async ({ page }) => {
        await installMockApi(page);
        await page.addInitScript((storedTheme: string) => {
          localStorage.setItem("cnp-theme", storedTheme);
        }, theme);
        await page.setViewportSize({ width: viewportWidth, height: 800 });
        await page.goto(path, { waitUntil: "networkidle" });

        const axeResults = await new AxeBuilder({ page })
          .withTags(WCAG_TAGS)
          .exclude("nextjs-portal")
          .analyze();

        scans.push({
          page: path,
          theme,
          viewportWidth,
          violations: axeResults.violations.map((violation) => ({
            id: violation.id,
            impact: violation.impact,
            help: violation.help,
            helpUrl: violation.helpUrl,
            nodeCount: violation.nodes.length,
            targets: violation.nodes.map((node) => node.target.join(" ")),
          })),
        });

        const criticalIds = axeResults.violations
          .filter((violation) => violation.impact === "critical")
          .map((violation) => violation.id);
        expect(criticalIds, `critical axe violations on ${path}`).toEqual([]);
      });
    }
  }
}

function renderMarkdown(results: ScanResult[], unmocked: string[]): string {
  const lines: string[] = ["# Accessibility report (axe, WCAG 2.2 A/AA)", ""];

  lines.push("## Violations per page", "", "| Page | Theme | Width | Violations | Critical | Serious |", "| --- | --- | --- | --- | --- | --- |");
  for (const scan of results) {
    const critical = scan.violations.filter((violation) => violation.impact === "critical").length;
    const serious = scan.violations.filter((violation) => violation.impact === "serious").length;
    lines.push(`| \`${scan.page}\` | ${scan.theme} | ${scan.viewportWidth}px | ${scan.violations.length} | ${critical} | ${serious} |`);
  }

  const byRule = new Map<string, { impact: string; nodes: number; pages: Set<string>; help: string; helpUrl: string }>();
  for (const scan of results) {
    for (const violation of scan.violations) {
      const entry = byRule.get(violation.id) ?? {
        impact: violation.impact ?? "unknown",
        nodes: 0,
        pages: new Set<string>(),
        help: violation.help,
        helpUrl: violation.helpUrl,
      };
      entry.nodes += violation.nodeCount;
      entry.pages.add(scan.page);
      byRule.set(violation.id, entry);
    }
  }

  lines.push("", "## Violations grouped by rule", "", "| Rule | Impact | Nodes | Pages | Description |", "| --- | --- | --- | --- | --- |");
  for (const [id, entry] of [...byRule.entries()].sort((a, b) => b[1].nodes - a[1].nodes)) {
    lines.push(`| [\`${id}\`](${entry.helpUrl}) | ${entry.impact} | ${entry.nodes} | ${entry.pages.size} | ${entry.help} |`);
  }

  lines.push("", "## Requests not covered by fixtures", "");
  if (unmocked.length === 0) {
    lines.push("None.");
  } else {
    for (const request of unmocked) lines.push(`- \`${request}\``);
  }
  lines.push("");
  return lines.join("\n");
}
