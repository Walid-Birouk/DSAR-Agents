import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function runPrepared(page: Page, caseId: string) {
  await page.goto("/");
  await page.getByLabel("Request", { exact: true }).selectOption(caseId);
  await page.getByRole("button", { name: "Run this request" }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f]+$/);
}

async function firstStandardCase(page: Page): Promise<string> {
  const presets = (await (await page.request.get("/api/presets?kind=try")).json()) as { case_id: string; kind: string }[];
  return presets.find((p) => p.kind === "standard")!.case_id;
}

test("a visitor runs a prepared request, reviews it and sends the reply", async ({ page }) => {
  await page.goto("/");
  await runPrepared(page, await firstStandardCase(page));
  const reviewer = page.getByRole("region", { name: "You are the reviewer" });
  await expect(reviewer).toBeVisible({ timeout: 45_000 });
  await expect(page.getByRole("heading", { name: "The reply" })).toBeVisible();
  const ack = reviewer.getByRole("checkbox");
  if (await ack.count()) await ack.check();
  await reviewer.getByRole("button", { name: "Approve and send" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Closed");
  const audit = page.getByRole("region", { name: "Audit log" });
  await expect(audit).toContainText("release");
  await expect(audit).toContainText("approve");
  await page.getByRole("link", { name: "See the full trace" }).click();
  await expect(page.getByRole("heading", { name: /Spans \(\d+\)/ })).toBeVisible();
  await expect(page.getByText("agent coordinator").first()).toBeVisible();
});

test("a break-it attack is blocked and counted", async ({ page }) => {
  await page.goto("/break");
  await page.getByRole("button", { name: /Try this attack/ }).first().click();
  await expect(page.getByRole("heading", { name: "What the agents did" })).toBeVisible({ timeout: 45_000 });
  await page.goto("/break");
  await expect(page.getByText(/Attempts so far: 1/)).toBeVisible();
  await expect(page.getByText(/blocked: 1/)).toBeVisible();
});

test("the other pages load", async ({ page }) => {
  await page.goto("/evals");
  await expect(page.getByRole("heading", { name: "Eval results" })).toBeVisible();
  await page.goto("/system");
  await expect(page.getByText("Model spend today")).toBeVisible();
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "My runs" })).toBeVisible();
});

test("the pages have no serious accessibility problems", async ({ page }) => {
  for (const path of ["/", "/break", "/evals", "/system", "/runs"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious.map((v) => `${path}: ${v.id} ${v.help}`)).toEqual([]);
  }
  await runPrepared(page, await firstStandardCase(page));
  await expect(page.getByRole("region", { name: "You are the reviewer" })).toBeVisible({ timeout: 45_000 });
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `run view: ${v.id} ${v.help}`)).toEqual([]);
});

test("everything works with the keyboard alone", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Try it" })).toBeFocused();
});
