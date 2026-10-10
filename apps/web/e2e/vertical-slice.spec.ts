import { expect, test, type Browser, type Page } from "@playwright/test";
import path from "node:path";

/**
 * The first vertical slice through the browser, one signed-in person per role. Everything runs for
 * real (worker, sandboxed tests, git, staging deployment) except the coding agent, which is the
 * labeled scripted fixture for the sample repository.
 */

const TOKENS = {
  owner: "dev_owner",
  engineer: "dev_engineer",
  reviewer: "dev_reviewer",
  qa: "dev_qa",
  approver: "dev_approver",
} as const;
type Who = keyof typeof TOKENS;

const BRD = path.resolve(import.meta.dirname, "../../../samples/documents/task-title-validation-brd.docx");

async function as(browser: Browser, who: Who): Promise<Page> {
  const ctx = await browser.newContext();
  await ctx.addInitScript((token) => sessionStorage.setItem("asw.token", token), TOKENS[who]);
  return ctx.newPage();
}

/** Reload until the assertion holds: the worker advances the run in the background. */
async function eventually(page: Page, check: () => Promise<void>, timeout = 180_000): Promise<void> {
  await expect(async () => {
    await page.reload();
    await check();
  }).toPass({ timeout, intervals: [2_000, 3_000, 5_000] });
}

test("BRD to approved staging release, driven through the UI", async ({ browser }) => {
  const name = `E2E task titles ${Date.now()}`;

  // 1. The owner creates and staffs the project and registers the sample repository.
  const owner = await as(browser, "owner");
  await owner.goto("/");
  await owner.getByRole("button", { name: "Create project" }).first().click();
  await owner.getByLabel("Name").fill(name);
  await owner
    .getByRole("dialog")
    .getByRole("button", { name: /create/i })
    .click();
  await expect(owner.getByRole("heading", { name })).toBeVisible();
  const base = new URL(owner.url()).pathname;

  await owner.goto(`${base}/settings`);
  for (const [email, role] of [
    ["engineer@example.dev", "engineer"],
    ["reviewer@example.dev", "reviewer"],
    ["qa@example.dev", "qa"],
    ["approver@example.dev", "release approver"],
  ] as const) {
    await owner.getByLabel("Email").fill(email);
    await owner.getByRole("combobox").first().click();
    await owner.getByRole("option", { name: role, exact: true }).click();
    await owner.getByRole("button", { name: "Add member" }).click();
    await expect(owner.getByRole("table", { name: "Members" })).toContainText(email);
  }
  await owner.getByLabel("Sample repository").fill("sample-task-service");
  await owner.getByRole("button", { name: "Register sample repository" }).click();
  await expect(owner.getByRole("table", { name: "Repositories" })).toContainText("sample-task-service");

  // 2-3. The engineer uploads the BRD; requirements appear with their source passages.
  const engineer = await as(browser, "engineer");
  await engineer.goto(`${base}/sources`);
  await engineer.getByLabel("Choose a file to upload").setInputFiles(BRD);
  await engineer.getByRole("button", { name: "Upload and extract" }).click();
  await expect(engineer.getByText("The Business Analyst agent is extracting requirements.")).toBeVisible();
  await engineer.goto(`${base}/requirements`);
  await eventually(engineer, async () => {
    await expect(engineer.getByText("FR-001", { exact: true }).first()).toBeVisible({ timeout: 2_000 });
  });
  await expect(engineer.getByText("NFR-002", { exact: true }).first()).toBeVisible();

  // 5-7. The engineer starts delivery; planner and architect run.
  await engineer.goto(`${base}/activity`);
  await engineer.getByRole("button", { name: "Start delivery run" }).click();

  // 8. A reviewer approves the exact architecture version; the agent cannot.
  const reviewer = await as(browser, "reviewer");
  await reviewer.goto(`${base}/approvals`);
  await eventually(reviewer, async () => {
    await expect(reviewer.getByRole("button", { name: /^Approve v\d+$/ })).toBeVisible({ timeout: 2_000 });
  });
  await reviewer.getByLabel("Comment").fill("Fits the service conventions.");
  await reviewer.getByRole("button", { name: /^Approve v\d+$/ }).click();

  // The owner approves the implementation scope.
  await owner.goto(`${base}/approvals`);
  await eventually(owner, async () => {
    await expect(owner.getByText(/implementation/i).first()).toBeVisible({ timeout: 2_000 });
    await expect(owner.getByRole("button", { name: /^Approve v\d+$/ })).toBeVisible({ timeout: 2_000 });
  });
  await owner.getByRole("button", { name: /^Approve v\d+$/ }).click();

  // 9-14. Implementation, QA, the defect and its repair run; then only a release approver may decide.
  const approver = await as(browser, "approver");
  await approver.goto(`${base}/approvals`);
  await eventually(
    approver,
    async () => {
      await expect(approver.getByRole("button", { name: /^Approve v\d+$/ })).toBeVisible({ timeout: 2_000 });
    },
    300_000,
  );
  await owner.goto(`${base}/approvals`);
  await expect(owner.getByText(/You can’t decide this/).first()).toBeVisible();
  await approver.getByLabel("Comment").fill("Ship to staging.");
  await approver.getByRole("button", { name: /^Approve v\d+$/ }).click();

  // 15. Staging deployment with recorded evidence; production stays disabled.
  await approver.goto(`${base}/releases`);
  await eventually(approver, async () => {
    await expect(approver.getByText("deployed staging").first()).toBeVisible({ timeout: 2_000 });
  });
  await expect(
    approver.getByText("Production deployment is disabled by policy for this workspace."),
  ).toBeVisible();

  const qa = await as(browser, "qa");
  await qa.goto(`${base}/quality`);
  await qa.getByRole("tab", { name: /Defects/ }).click();
  await expect(qa.getByText(/found in attempt 1 · fixed in attempt 2/)).toBeVisible();
  await qa.goto(`${base}/implementation`);
  await expect(qa.getByText("Scripted fixture").first()).toBeVisible();
});
