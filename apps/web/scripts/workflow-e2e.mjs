// Analyst and reviewer workflow, end to end, against a running instance with the demo organisation
// loaded and the worker and scheduler running:
//   1. an analyst corrects an incident in the browser; the worker re-derives it (new version, back in
//      review, correction alert held for human review because it is high impact);
//   2. a reviewer approves the held alert and the scheduler delivers it (email via SMTP);
//   3. publishing the daily brief that cites the corrected incident requires acknowledging the change;
//      the client role can then read the published brief;
//   4. the analyst removes their override and the derived values come back.
//
//   BASE_URL=http://localhost:3000 DEMO_PASSWORD=... [MAIL_DIR=../../var/mail] node scripts/workflow-e2e.mjs
// Run it against a fresh demo (`gccintel demo-setup && gccintel demo-run`): it changes data.
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const PASSWORD = process.env.DEMO_PASSWORD;
const MAIL_DIR = process.env.MAIL_DIR;
if (!PASSWORD) {
  console.error("Set DEMO_PASSWORD to the password used by `gccintel demo-setup`.");
  process.exit(2);
}

const failures = [];
const check = (cond, msg) => {
  console.log(`  ${cond ? "ok  " : "FAIL"} ${msg}`);
  if (!cond) failures.push(msg);
  return cond;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, timeoutMs = 90_000, everyMs = 2_000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const v = await fn();
    if (v) return v;
    await sleep(everyMs);
  }
  throw new Error(`timed out after ${timeoutMs / 1000}s waiting for ${what}`);
}

async function signIn(browser, role) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => check(false, `${role}: browser error ${e.message}`));
  await page.goto(`${BASE}/login`);
  await page.fill('input[name="email"]', `${role}@demo.example`);
  await page.fill('input[name="password"]', PASSWORD);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15_000 }), page.click('button[type="submit"]')]);
  return page;
}
const incidents = async (page) => (await (await page.request.get(`${BASE}/api/v1/incidents?range=90d&limit=200`)).json()).data;
const notice = (form, re) => form.getByText(re).first().waitFor({ timeout: 20_000 }).then(() => true, () => false);
const mailCount = () => (MAIL_DIR ? readdirSync(MAIL_DIR).filter((f) => f.endsWith(".eml")).length : 0);

const browser = await chromium.launch();
try {
  // 1. Analyst correction -> worker re-derivation
  const analyst = await signIn(browser, "analyst");
  const all = await incidents(analyst);
  const target = all.find((i) => /Ahmadi/.test(i.title));
  if (!target) throw new Error("demo incident about the Ahmadi substation not found; run the demo first");
  console.log(`\n1. analyst corrects ${target.ref} (severity ${target.severity}, version ${target.version})`);
  await analyst.goto(`${BASE}/incidents/${target.id}`);
  let form = analyst.locator("form", { has: analyst.getByRole("button", { name: "Save correction" }) });
  await form.locator('select[name="severity"]').selectOption("5");
  await form.locator('input[name="severity_reason"]').fill("Operator confirmed grid-wide impact (workflow test)");
  await form.locator('textarea[name="note"]').fill("Workflow test: severity raised after operator statement");
  await form.getByRole("button", { name: "Save correction" }).click();
  check(await notice(form, /Correction saved/), "correction saved and queued for the worker");
  const edited = await until(async () => (await incidents(analyst)).find((i) => i.id === target.id && i.version > target.version), "the worker to re-derive the incident");
  check(edited.severity === 5, `worker re-derived ${target.ref}: version ${target.version} → ${edited.version}, severity ${edited.severity}`);
  check(edited.review_status === "pending", `incident is back in review (${edited.review_status})`);
  await analyst.goto(`${BASE}/incidents/${target.id}`);
  let body = await analyst.locator("body").innerText();
  check(/Severity \(analyst\)/.test(body), "page labels the severity override as the analyst's");
  check(/Location \(publisher correction\)/.test(body), "the publisher's earlier location correction is kept and labelled");

  // 2. Reviewer approves the held correction alert; the scheduler delivers it
  console.log("\n2. reviewer approves the held correction alert");
  const reviewer = await signIn(browser, "reviewer");
  await reviewer.goto(`${BASE}/alerts`);
  const held = reviewer.locator("details").filter({ hasText: target.ref }).filter({ hasText: "Correction" })
    .filter({ has: reviewer.getByRole("button", { name: "Approve and send" }) });
  check((await held.count()) === 1, `one high-impact correction alert is held for review, not sent (${await held.count()})`);
  const before = mailCount();
  await held.getByRole("button", { name: "Approve and send" }).click();
  // The review form disappears once the alert is no longer pending; its status line is the confirmation.
  const decided = reviewer.locator("details").filter({ hasText: target.ref }).filter({ hasText: "approved by reviewer" });
  check(await decided.first().waitFor({ timeout: 20_000 }).then(() => true, () => false), "alert approved by the reviewer");
  if (MAIL_DIR) {
    const mail = await until(() => {
      const files = readdirSync(MAIL_DIR).filter((f) => f.endsWith(".eml")).sort();
      return files.length > before ? readFileSync(`${MAIL_DIR}/${files.at(-1)}`, "utf8") : null;
    }, "the scheduler to deliver the approved alert", 120_000, 3_000);
    check(/CORRECTION/.test(mail), "correction email delivered through SMTP");
  }
  await reviewer.goto(`${BASE}/alerts`);
  const sent = reviewer.locator("details").filter({ hasText: target.ref }).filter({ hasText: "approved by reviewer" });
  check((await sent.count()) >= 1 && /sent/i.test(await sent.first().innerText()), "delivery is logged on the alert");

  // 3. Publishing the brief that cites the corrected incident
  console.log("\n3. reviewer publishes the daily brief");
  await reviewer.goto(`${BASE}/reports`);
  const href = await reviewer.locator('a[href^="/reports/"]').first().getAttribute("href");
  await reviewer.goto(`${BASE}${href}`);
  body = await reviewer.locator("body").innerText();
  check(body.includes(`${target.ref} (v`) && /changed after version/.test(body), "report warns that a cited incident changed after it was written");
  form = reviewer.locator("form", { has: reviewer.getByRole("button", { name: "Publish", exact: true }) });
  await form.locator('input[name="note"]').fill("Workflow test review");
  const ack = form.locator('input[name="acknowledge_changes"]');
  check((await ack.count()) === 1, "publishing asks the reviewer to confirm they checked the change");
  await ack.check();
  await form.getByRole("button", { name: "Publish", exact: true }).click();
  check(await reviewer.getByText(/published .* by reviewer@demo\.example/).first().waitFor({ timeout: 20_000 }).then(() => true, () => false),
    "report published, with the reviewer recorded");
  const client = await signIn(browser, "client");
  const res = await client.goto(`${BASE}${href}`);
  check(res.status() === 200 && /Citation check passed/.test(await client.locator("body").innerText()), "client can read the published brief with its citation check");

  // 4. Removing the analyst's override restores the derived severity
  console.log("\n4. analyst removes their override");
  await analyst.goto(`${BASE}/incidents/${target.id}`);
  form = analyst.locator("form", { has: analyst.getByRole("button", { name: "Save correction" }) });
  await form.locator('input[name="remove_overrides"]').check();
  await form.locator('textarea[name="note"]').fill("Workflow test: override withdrawn");
  await form.getByRole("button", { name: "Save correction" }).click();
  check(await notice(form, /Correction saved/), "override removal saved");
  const restored = await until(async () => (await incidents(analyst)).find((i) => i.id === target.id && i.version > edited.version), "the worker to re-derive again");
  check(restored.severity === target.severity, `severity back to the computed ${restored.severity}`);
  await analyst.goto(`${BASE}/incidents/${target.id}`);
  body = await analyst.locator("body").innerText();
  check(!/Severity \(analyst\)/.test(body) && /Location \(publisher correction\)/.test(body), "analyst override gone; publisher correction still in effect");
} catch (e) {
  check(false, e.message);
} finally {
  await browser.close();
}

console.log(failures.length ? `\n${failures.length} failure(s)` : "\nWorkflow test passed");
process.exit(failures.length ? 1 : 0);
