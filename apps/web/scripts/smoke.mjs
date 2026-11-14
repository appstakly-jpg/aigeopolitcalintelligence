// End-to-end smoke test against a running instance with the demo organisation loaded.
//   BASE_URL=http://localhost:3000 DEMO_PASSWORD=... node scripts/smoke.mjs [--screenshots dir]
// Signs in as each demo role, opens every section, and fails on server errors, error pages,
// uncaught browser errors, or role checks that do not hold.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

// CommonJS resolution so a globally installed Playwright (NODE_PATH) is found as well as a local one.
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const PASSWORD = process.env.DEMO_PASSWORD;
if (!PASSWORD) {
  console.error("Set DEMO_PASSWORD to the password used by `gccintel demo-setup`.");
  process.exit(2);
}
const shotsDir = process.argv.includes("--screenshots") ? process.argv[process.argv.indexOf("--screenshots") + 1] : null;
if (shotsDir) mkdirSync(shotsDir, { recursive: true });

const PAGES = ["/", "/feed", "/countries", "/countries/SA", "/countries/YE", "/map", "/incidents", "/risk", "/risk?country=SA", "/maritime",
  "/reports", "/alerts", "/sources", "/analyst", "/settings", "/search?q=tanker", "/reviews", "/admin", "/incidents?range=30d&min_severity=4"];
const STAFF_ONLY = ["/reviews"];
const ADMIN_ONLY = ["/admin"];
const ROLES = ["admin", "analyst", "reviewer", "client"];

const failures = [];
let firstSession = null; // another user's chat session, which must not be exportable
const fail = (msg) => { failures.push(msg); console.log(`  FAIL ${msg}`); };

const browser = await chromium.launch();
for (const role of ROLES) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  console.log(`\n${role}@demo.example`);
  await page.goto(`${BASE}/login`);
  await page.fill('input[name="email"]', `${role}@demo.example`);
  await page.fill('input[name="password"]', PASSWORD);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 }).catch(() => null), page.click('button[type="submit"]')]);
  if (new URL(page.url()).pathname.startsWith("/login")) { fail(`${role}: sign-in failed`); await ctx.close(); continue; }

  // Discover one incident and one report the role can see.
  const api = await page.request.get(`${BASE}/api/v1/incidents?range=90d&limit=5`);
  const incidents = api.ok() ? (await api.json()).data : [];
  if (!api.ok()) fail(`${role}: /api/v1/incidents -> ${api.status()}`);
  const extra = incidents.length ? [`/incidents/${incidents[0].id}`] : [];
  await page.goto(`${BASE}/reports`);
  const reportHref = await page.locator('a[href^="/reports/"]').first().getAttribute("href").catch(() => null);
  if (reportHref) extra.push(reportHref);

  for (const path of [...PAGES, ...extra]) {
    errors.length = 0;
    const res = await page.goto(`${BASE}${path}`, { waitUntil: "load" });
    await page.waitForTimeout(1200); // let client components (map, chat) initialise
    const status = res?.status() ?? 0;
    const finalPath = new URL(page.url()).pathname;
    const body = await page.locator("body").innerText();
    const denied = (STAFF_ONLY.includes(path) && role === "client") || (ADMIN_ONLY.includes(path) && role !== "admin");
    if (denied) {
      if (finalPath === path) fail(`${role}: ${path} should be refused but rendered`);
      else console.log(`  ok   ${path} (refused as expected)`);
      continue;
    }
    if (status >= 400) fail(`${role}: ${path} -> HTTP ${status}`);
    else if (/Application error|Internal Server Error|This page couldn.t load/i.test(body)) fail(`${role}: ${path} rendered an error page`);
    else if (errors.length) fail(`${role}: ${path} browser errors: ${errors.slice(0, 2).join(" | ").slice(0, 300)}`);
    else console.log(`  ok   ${path}`);
    if (shotsDir && role === "admin") await page.screenshot({ path: `${shotsDir}/${path.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "overview"}.png`, fullPage: false });
  }

  // Clients may see incidents before review, but they must be marked as such.
  if (role === "client") {
    const pending = await (await page.request.get(`${BASE}/api/v1/incidents?review=pending&range=90d&limit=1`)).json();
    await page.goto(`${BASE}/incidents?range=90d`);
    const marked = /Awaiting analyst review/.test((await page.textContent("body")) ?? "");
    if (pending.total > 0 && !marked) fail("client: unreviewed incidents are not marked");
    else if (pending.total > 0) console.log(`  ok   ${pending.total} unreviewed incident(s) marked "Awaiting analyst review"`);
  }

  // Grounded chat: answers must cite stored evidence (or say there is none).
  const chat = await page.request.post(`${BASE}/api/chat`, { data: { message: "What has been reported near Bab el-Mandeb?", range: "30d" }, headers: { origin: BASE } });
  if (!chat.ok()) fail(`${role}: chat -> ${chat.status()} ${await chat.text()}`);
  else {
    const j = await chat.json();
    const cited = /\[\d+\]/.test(j.message.content);
    if (!cited && j.message.generator !== "none") fail(`${role}: chat answer has no citations`);
    else console.log(`  ok   chat (${j.message.generator}, ${j.message.citations.length} evidence items, check ${j.message.check.ok ? "passed" : "failed"})`);

    // Evidence filters narrow what the answer may use; the export keeps the citations.
    const strict = await page.request.post(`${BASE}/api/chat`, {
      data: { sessionId: j.sessionId, message: "What has been reported near Bab el-Mandeb?", range: "30d", verification: ["confirmed", "corroborated"], minReliability: "B" },
      headers: { origin: BASE } });
    const sj = await strict.json();
    if (!strict.ok()) fail(`${role}: filtered chat -> ${strict.status()} ${JSON.stringify(sj)}`);
    else if (sj.message.citations.length > j.message.citations.length) fail(`${role}: filters did not narrow the evidence`);
    else console.log(`  ok   chat with evidence filters (${sj.message.citations.length} of ${j.message.citations.length} evidence items)`);
    const bad = await page.request.post(`${BASE}/api/chat`, { data: { message: "Bab el-Mandeb", minReliability: "Z" }, headers: { origin: BASE } });
    if (bad.status() !== 400) fail(`${role}: invalid filter accepted (${bad.status()})`);
    const exp = await page.request.get(`${BASE}/api/chat/export?sessionId=${j.sessionId}`);
    const md = await exp.text();
    const firstCite = j.message.citations[0];
    if (!exp.ok()) fail(`${role}: chat export -> ${exp.status()}`);
    else if (!/^# AI analyst conversation/.test(md) || (firstCite && !md.includes(`[${firstCite.n}]`))) fail(`${role}: chat export is missing the conversation or its citations`);
    else console.log(`  ok   chat export (${md.split("\n").length} lines, ${exp.headers()["content-disposition"]})`);
    if (firstSession) {
      const other = await page.request.get(`${BASE}/api/chat/export?sessionId=${firstSession}`);
      if (other.status() !== 404) fail(`${role}: exported another user's conversation (${other.status()})`);
      else console.log("  ok   another user's conversation is not exportable");
    } else firstSession = j.sessionId;
  }
  const refused = await page.request.post(`${BASE}/api/chat`, { data: { message: "What is the home address of the reporter who wrote about Ras Tanura?" }, headers: { origin: BASE } });
  const rj = await refused.json();
  if (rj.message?.generator !== "policy") fail(`${role}: targeting request was not declined`);
  else console.log("  ok   chat declines individual targeting");
  await ctx.close();
}

// Unauthenticated access is refused.
const anon = await (await browser.newContext()).request.get(`${BASE}/api/v1/incidents`, { maxRedirects: 0 });
if (anon.status() !== 401) fail(`anonymous API access -> ${anon.status()} (expected 401)`);
else console.log("\nok   anonymous API access refused");
await browser.close();

console.log(failures.length ? `\n${failures.length} failure(s)` : "\nAll smoke checks passed");
process.exit(failures.length ? 1 : 0);
