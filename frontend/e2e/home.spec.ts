import { expect, test } from "@playwright/test";

// The one smoke test: the app shell actually boots against a browser, against
// the real production build. Everything past "does it render" is covered by
// the unit/component suite — this exists to catch what those cannot: a
// bundling break, a routing misconfiguration, a blank white screen.
test("home page loads, shows the goal input, and prompts for a provider", async ({ page }) => {
  await page.route("**/api/v1/identity", (route) =>
    route.fulfill({ json: { token: "e2e-test-token" } }),
  );
  await page.route("**/api/v1/runs*", (route) =>
    route.fulfill({ json: { runs: [], total: 0 } }),
  );

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "What should the agent do?" })).toBeVisible();
  await expect(page.getByPlaceholder(/Ask anything about Sitare/)).toBeVisible();

  // No provider is configured on a fresh browser context (sessionStorage is
  // empty), so the connect-a-provider nudge and a disabled submit are exactly
  // what a first-time visitor should see.
  await expect(page.getByRole("link", { name: /Connect an AI provider/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run" })).toBeDisabled();
});
