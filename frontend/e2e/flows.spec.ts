import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function runPrepared(page: Page, caseId: string) {
  await page.goto("/try");
  await page.getByLabel("Request", { exact: true }).selectOption(caseId);
  await page.getByRole("button", { name: "Run this request" }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f]+$/);
}

async function firstStandardCase(page: Page): Promise<string> {
  const presets = (await (await page.request.get("/api/presets?kind=try")).json()) as { case_id: string; kind: string }[];
  return presets.find((p) => p.kind === "standard")!.case_id;
}

test("the first page shows the guard layer with live numbers and real catches", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("doesn't let them through");
  await expect(page.getByText("times code refused the model")).toBeVisible();
  const layer = page.getByRole("region", { name: "The guard layer, and what each guard caught" });
  await expect(layer.getByRole("button", { name: /Other people's details are hidden/ })).toHaveAttribute("aria-expanded", "true");
  await expect(layer.getByText("Code refused:").first()).toBeVisible();
  await expect(page.getByRole("region", { name: /What it costs/ })).toContainText("Not measured");
  await page.getByRole("link", { name: "Follow one request" }).click();
  await expect(page).toHaveURL(/\/story$/);
});

test("the guided story shows the careless answer, replays the desk and lets the visitor send", async ({ page }) => {
  await page.goto("/story");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("without leaking someone else's");
  const careless = page.locator("#step-2");
  await expect(careless).toContainText("8 of them are about other people");
  await expect(careless).toContainText("FL-100514");
  await page.getByRole("button", { name: /Run the desk on/ }).click();
  const desk = page.locator("#step-3");
  await expect(desk).toContainText("the AI slipped, and the code caught it", { timeout: 45_000 });
  await expect(desk).toContainText("the release guard refused", { timeout: 45_000 });
  await page.getByRole("button", { name: "Approve and send" }).click();
  await expect(page.getByText(/^Sent to/)).toBeVisible();
});

test("a visitor runs a prepared request, reviews it and sends the reply", async ({ page }) => {
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

async function caseOfKind(page: Page, kind: string): Promise<string> {
  const presets = (await (await page.request.get("/api/presets?kind=try")).json()) as { case_id: string; kind: string }[];
  return presets.find((p) => p.kind === kind)!.case_id;
}

test("a request with no account gets the Art. 12(4) refusal letter, which the reviewer sends", async ({ page }) => {
  await runPrepared(page, await caseOfKind(page, "identity_no_account"));
  const refusal = page.getByRole("region", { name: "The refusal letter (GDPR Art. 12(4))" });
  await expect(refusal).toBeVisible({ timeout: 45_000 });
  await expect(refusal).toContainText("CNIL");
  await refusal.getByRole("button", { name: "Send the refusal letter" }).click();
  await expect(refusal.getByText("Sent to the requester.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Audit log" })).toContainText("send_refusal");
});

test("code sends a repeat request to a person, with the reason and the advisor's view", async ({ page }) => {
  await runPrepared(page, await caseOfKind(page, "edge_repeat"));
  const person = page.getByRole("region", { name: "With a person" });
  await expect(person).toBeVisible({ timeout: 45_000 });
  await expect(person).toContainText("R-EXCESSIVE");
  await expect(person).toContainText("DPO advisor");
});

test("the copy downloads as a PDF", async ({ page }) => {
  await runPrepared(page, await firstStandardCase(page));
  await expect(page.getByRole("region", { name: "You are the reviewer" })).toBeVisible({ timeout: 45_000 });
  const href = await page.getByRole("link", { name: "Download the copy (PDF)" }).getAttribute("href");
  const pdf = await page.request.get(href!);
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 8).toString()).toBe("%PDF-1.4");
});

test("a break-it attack is blocked and counted", async ({ page }) => {
  // The counters are per day and the e2e server's directory is reused, so compare before and after.
  const before = (await (await page.request.get("/api/system")).json()) as { attempts: number; attempts_blocked: number };
  await page.goto("/break");
  await page.getByRole("button", { name: /Try this attack/ }).first().click();
  await expect(page.getByRole("heading", { name: "What the agents did" })).toBeVisible({ timeout: 45_000 });
  await page.goto("/break");
  await expect(page.getByText(`Attempts so far: ${before.attempts + 1}`)).toBeVisible();
  await expect(page.getByText(`blocked: ${before.attempts_blocked + 1}`)).toBeVisible();
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
  for (const path of ["/", "/story", "/try", "/break", "/evals", "/system", "/runs"]) {
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
  await expect(page.getByRole("link", { name: "The harness" })).toBeFocused();
});
