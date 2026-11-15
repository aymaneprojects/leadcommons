#!/usr/bin/env node
/*
 * Parity shots: put LeadCommons in the same state as each reference screenshot and capture it.
 *
 * Usage: node tools/ux_audit/parity.cjs --base http://127.0.0.1:8171 --empty-base http://127.0.0.1:8173 --out /tmp/parity
 * Writes <out>/<id>.png for every scene below and <out>/scenes.json (id, title, ok, note).
 * Each scene runs in a fresh browser context so UI state never leaks between scenes.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const arg = (n, d) => (process.argv.includes(`--${n}`) ? process.argv[process.argv.indexOf(`--${n}`) + 1] : d);
const BASE = arg("base", "http://127.0.0.1:8171").replace(/\/$/, "");
const OUT = arg("out", "parity");
// Empty-state scenes (lists, sequences, emails, tasks) run against a server with an empty home.
const EMPTY = arg("empty-base", BASE).replace(/\/$/, "");

// Helpers used by scenes.
const go = (p, url, base = BASE) =>
  p.goto(base + url, { waitUntil: "networkidle" }).catch(() => p.goto(base + url));
const clickText = async (p, re, scope = "body") => {
  const loc = p.locator(scope).locator("button, summary, a, [role=button], [role=tab], label").filter({ hasText: re }).first();
  await loc.click({ timeout: 4000 });
  await p.waitForTimeout(500);
};
const checkRows = async (p, n) => {
  const boxes = p.locator("table tbody input[type=checkbox]");
  for (let i = 0; i < n; i++) await boxes.nth(i).check({ timeout: 3000 });
  await p.waitForTimeout(400);
};
const firstLink = async (p, sel) => {
  await p.locator(sel).first().click({ timeout: 4000 });
  await p.waitForTimeout(900);
};

const SCENES = [
  ["p0-01", "Assistant conversation", async (p) => {
    await go(p, "/assistant");
    const box = p.locator("#composer textarea, textarea#ask").first();
    await box.fill("Find fintech companies in France");
    await p.locator("#composer button[type=submit], #composer button").last().click();
    await p.waitForTimeout(2500);
  }],
  ["p0-02", "Home, sidebar collapsed", async (p) => {
    await p.addInitScript(() => { try { localStorage.setItem("lc-nav", "collapsed"); } catch (e) {} });
    await go(p, "/");
  }],
  ["p0-03", "Home, sidebar expanded", async (p) => go(p, "/")],
  ["p0-04", "Find people", async (p) => go(p, "/people")],
  ["p0-05", "Find companies", async (p) => go(p, "/companies")],
  ["p1-01", "People results with a title filter", async (p) => go(p, "/people?title=Head")],
  ["p1-02", "People, 3 rows selected", async (p) => { await go(p, "/people?all=1"); await checkRows(p, 3); }],
  ["p1-03", "Person page", async (p) => {
    await go(p, "/people?all=1");
    const href = await p.locator("table tbody a[href^='/people/']").first().getAttribute("href");
    await go(p, href);
  }],
  ["p1-04", "Company panel", async (p) => { await go(p, "/companies?q=bank"); await firstLink(p, "table tbody a.co-name"); }],
  ["p2-05", "Job titles filter open", async (p) => { await go(p, "/people?title=Head"); await clickText(p, /job titles/i, ".fpanel"); }],
  ["p2-06", "Location filter open", async (p) => { await go(p, "/people?title=Head"); await clickText(p, /^\s*location/i, ".fpanel"); }],
  ["p2-08", "Industry & keywords filter open", async (p) => { await go(p, "/people?title=Head"); await clickText(p, /industry/i, ".fpanel"); }],
  ["p2-09", "All filters window", async (p) => { await go(p, "/people?title=Head"); await clickText(p, /view all filters|all filters/i); }],
  ["p2-10", "# Employees in the all-filters window", async (p) => {
    await go(p, "/people?title=Head"); await clickText(p, /view all filters|all filters/i);
    await p.locator("#allf-dialog summary, #allf-dialog button").filter({ hasText: /employees/i }).first().click(); await p.waitForTimeout(500);
  }],
  ["p2-11", "Default view menu", async (p) => { await go(p, "/companies"); await p.locator("details.view-dd > summary").first().click(); await p.waitForTimeout(500); }],
  ["p2-12", "Search settings", async (p) => { await go(p, "/companies"); await p.locator("[aria-label='Search settings'], [title='Search settings']").first().click(); await p.waitForTimeout(400); }],
  ["p2-13", "Column chooser", async (p) => { await go(p, "/companies?all=1"); await clickText(p, /add column/i); }],
  ["p2-14", "Action bar, 3 rows", async (p) => { await go(p, "/companies?all=1"); await checkRows(p, 3); }],
  ["p2-15", "Save to list window", async (p) => { await go(p, "/companies?all=1"); await checkRows(p, 3); await p.locator('.selbar [data-open-dialog="save-dialog"]').first().click(); await p.waitForTimeout(800); }],
  ["p2-16", "Import menu", async (p) => { await go(p, "/companies"); await clickText(p, /^\s*import/i); }],
  ["p2-17", "Export tooltip", async (p) => {
    await go(p, "/companies?all=1"); await checkRows(p, 3);
    await p.locator('.selbar [aria-label="Export CSV"]').first().hover(); await p.waitForTimeout(700);
  }],
  ["p3-18", "AI menu on a selection", async (p) => {
    await go(p, "/people?all=1"); await checkRows(p, 2);
    await p.locator(".selbar details.ai-dd > summary").first().click(); await p.waitForTimeout(500);
  }],
  ["p3-19", "Research with AI menu", async (p) => { await go(p, "/people?all=1"); await p.locator(".toolbar details.ai-dd > summary").first().click(); await p.waitForTimeout(500); }],
  ["p3-20", "Lists (empty)", async (p) => go(p, "/lists?tab=companies", EMPTY)],
  ["p4-21", "Sequences (empty)", async (p) => go(p, "/sequences", EMPTY)],
  ["p4-22", "Emails (empty)", async (p) => go(p, "/emails", EMPTY)],
  ["p4-23", "Tasks (empty)", async (p) => go(p, "/tasks", EMPTY)],
  ["p4-24", "Ctrl+K palette", async (p) => { await go(p, "/tasks", EMPTY); await p.keyboard.press("Control+k"); await p.waitForTimeout(800); }],
  ["p4-25", "Notifications", async (p) => { await go(p, "/tasks", EMPTY); await p.locator("[aria-label*='otification'], [title*='otification']").first().click(); await p.waitForTimeout(800); }],
  ["p4-26", "Settings menu", async (p) => { await go(p, "/tasks", EMPTY); await p.locator("button[data-settings-pop], aside [aria-label='Settings']").first().click(); await p.waitForTimeout(600); }],
  ["p4-27", "Settings overview", async (p) => go(p, "/settings")],
  ["p4-28", "Outbound settings", async (p) => go(p, "/settings/sending")],
  ["p4-29", "Email setup and health", async (p) => go(p, "/settings/email-health?tab=domains", EMPTY)],
  ["p4-30", "Onboarding hub (top)", async (p) => go(p, "/onboarding")],
  ["p4-31", "Onboarding hub (bottom)", async (p) => { await go(p, "/onboarding"); await p.evaluate(() => { const m = document.scrollingElement; m.scrollTop = m.scrollHeight; document.querySelectorAll("main, .main, .content").forEach((e) => { e.scrollTop = e.scrollHeight; }); }); await p.waitForTimeout(500); }],
  ["p4-32", "Assistant side panel (empty)", async (p) => { await go(p, "/"); await p.locator("[data-assistant-panel]").first().click(); await p.waitForTimeout(1500); }],
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const report = [];
  for (const [id, title, run] of SCENES) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const p = await ctx.newPage();
    const errors = [];
    p.on("pageerror", (e) => errors.push(String(e.message).slice(0, 160)));
    let ok = true;
    let note = "";
    try {
      await run(p);
    } catch (e) {
      ok = false;
      note = String(e.message).split("\n")[0].slice(0, 200);
    }
    await p.screenshot({ path: path.join(OUT, `${id}.png`) }).catch(() => {});
    report.push({ id, title, ok, note, errors });
    console.log(`${ok ? "ok  " : "FAIL"} ${id} ${title}${note ? " — " + note : ""}${errors.length ? " [js: " + errors[0] + "]" : ""}`);
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, "scenes.json"), JSON.stringify(report, null, 2));
})();
