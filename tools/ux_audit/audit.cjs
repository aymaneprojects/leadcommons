#!/usr/bin/env node
/*
 * Every-button UX audit for Pépite.
 *
 * Visits every page reachable from the app's own links, then clicks every visible control on
 * each page from a fresh page load and records what happened: navigation, download, dialog,
 * menu (whose items are then clicked too), HTTP errors, UI updates, console errors, clicks
 * swallowed by an overlay, or no effect at all (a dead button).
 *
 * Usage:
 *   node tools/ux_audit/audit.cjs --base http://127.0.0.1:8170 --out /tmp/audit
 *        [--max-pages 80] [--max-controls 120] [--pages /,/companies]
 * Writes <out>/report.json and <out>/report.md. Exit code 1 when problems are found.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
}

const BASE = arg("base", "http://127.0.0.1:8170").replace(/\/$/, "");
const OUT = arg("out", "ux-audit");
const MAX_PAGES = Number(arg("max-pages", 80));
const MAX_CONTROLS = Number(arg("max-controls", 120));
const START_PAGES = (arg("pages", "/") || "/").split(",");
// /partials/ are HTML fragments loaded into drawers and dialogs: opened alone they have no
// page script, so their controls are checked through the page that opens them instead.
const SKIP_HREF = /^\/(static|api|partials)\/|\/logout$|\/u\/|\.(csv|ics|json)(\?|$)|^\/opt-out/;
const SKIP_TEXT = /^(log ?out|sign out)$/i;

// A path pattern so detail pages are visited once (e.g. /companies/:id).
function pattern(p) {
  return p
    .split("/")
    .map((seg) => (/^[a-z]{2,4}_[0-9a-f]{8,}$|^[0-9a-f]{10,}$|^\d+$/.test(seg) ? ":id" : seg))
    .join("/");
}

async function discover(ctx) {
  const page = await ctx.newPage();
  const seen = new Map(); // pattern -> url
  const queue = [...START_PAGES];
  while (queue.length && seen.size < MAX_PAGES) {
    const url = queue.shift();
    const key = pattern(new URL(url, BASE).pathname);
    if (seen.has(key)) continue;
    let resp;
    try {
      resp = await page.goto(BASE + url, { waitUntil: "domcontentloaded", timeout: 15000 });
    } catch (e) {
      seen.set(key, { url, status: "load-error" });
      continue;
    }
    seen.set(key, { url, status: resp ? resp.status() : 0 });
    const hrefs = await page.$$eval("a[href]", (as) => as.map((a) => a.getAttribute("href")));
    for (const h of hrefs) {
      if (!h || !h.startsWith("/") || h.startsWith("//") || SKIP_HREF.test(h)) continue;
      const clean = h.split("#")[0];
      const k = pattern(new URL(clean, BASE).pathname);
      if (!seen.has(k)) queue.push(clean);
    }
  }
  await page.close();
  return [...seen.values()];
}

// Runs in the page: list visible controls with a stable CSS path.
function listControls(scopeSel) {
  const scope = scopeSel ? document.querySelector(scopeSel) : document;
  if (!scope) return [];
  const sel =
    "a[href], button, summary, [role=button], [role=menuitem], [role=tab], " +
    "input[type=checkbox], input[type=radio], select, label.chip, [data-open-dialog]";
  const path = (el) => {
    const parts = [];
    let anchored = false;
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      if (n.id && !/^\d/.test(n.id) && document.querySelectorAll(`#${CSS.escape(n.id)}`).length === 1) {
        parts.unshift(`#${CSS.escape(n.id)}`);
        anchored = true;
        break;
      }
      let i = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling)
        if (s.tagName === n.tagName) i++;
      parts.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${i})`);
    }
    // Anchor to body: an unanchored nth-of-type chain can also match hidden elements elsewhere.
    return (anchored ? "" : "body > ") + parts.join(" > ");
  };
  const out = [];
  for (const el of scope.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.right < 0 || r.bottom < 0) continue;
    // Hidden by an ancestor (closed menu, opacity 0, visibility hidden) or a skip link off-screen.
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true }))
      continue;
    const row = el.closest("tbody tr");
    if (row && [...row.parentElement.children].indexOf(row) > 1) continue; // first 2 rows only
    const text = (el.getAttribute("aria-label") || el.innerText || el.title || el.value || "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 70);
    out.push({
      sel: path(el),
      tag: el.tagName.toLowerCase(),
      text,
      disabled: !!(el.disabled || el.getAttribute("aria-disabled") === "true"),
      // A reason is a native title, our CSS tooltip (data-tip), aria-describedby text, or the
      // same on a wrapper (disabled buttons get no hover in some browsers, so wrappers carry it).
      reason:
        el.title ||
        el.getAttribute("data-tip") ||
        el.getAttribute("data-reason") ||
        (el.getAttribute("aria-describedby") || "")
          .split(/\s+/)
          .map((id) => (id && document.getElementById(id)?.textContent?.trim()) || "")
          .join(" ")
          .trim() ||
        [el.parentElement, el.parentElement?.parentElement]
          .map((w) => (w && (w.getAttribute("title") || w.getAttribute("data-tip"))) || "")
          .find(Boolean) ||
        "",
      shared: !!el.closest("aside, .sidebar, header.topbar, .topbar"),
      href: el.tagName === "A" ? el.getAttribute("href") : null,
      download: el.tagName === "A" && (el.hasAttribute("download") || /export|\.csv|\.ics/i.test(el.getAttribute("href") || "")),
    });
  }
  return out;
}

// Each probe gets a fresh browser context: the app remembers UI state (collapsed groups, hidden
// filters) in localStorage, which would otherwise leak from one click into the next probe.
async function probe(browser, url, control, pre) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  const responses = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text().slice(0, 200)));
  page.on("pageerror", (e) => errors.push(`pageerror: ${String(e.message).slice(0, 200)}`));
  const result = { url, sel: control.sel, text: control.text, tag: control.tag };
  try {
    await page.goto(BASE + url, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForTimeout(150);
    if (pre) {
      await page.locator(pre).first().click({ timeout: 2500 });
      await page.waitForTimeout(250);
    }
    errors.length = 0;
    await page.evaluate(() => {
      window.__mut = 0;
      new MutationObserver((m) => (window.__mut += m.length)).observe(document.body, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true,
      });
    });
    const before = await page.evaluate(() => ({
      href: location.href,
      dialogs: document.querySelectorAll("dialog[open], [role=dialog]:not([hidden])").length,
      details: document.querySelectorAll("details[open]").length,
    }));
    page.on("response", (r) => {
      if (r.url().startsWith(BASE)) responses.push({ url: r.url().slice(BASE.length), status: r.status() });
    });
    const loc = page.locator(control.sel).first();
    if ((await loc.count()) === 0) return { ...result, outcome: "not-found" };
    const download = page.waitForEvent("download", { timeout: 4000 }).catch(() => null);
    try {
      await loc.click({ timeout: 8000 });
    } catch (e) {
      const msg = String(e.message);
      return { ...result, outcome: /intercepts pointer events/.test(msg) ? "blocked-by-overlay" : "click-error", detail: msg.split("\n").filter((l) => l.trim()).slice(0, 8).join(" / ").slice(0, 600) };
    }
    const dl = await Promise.race([download, page.waitForTimeout(900).then(() => null)]);
    await page.waitForTimeout(300);
    let after;
    try {
      after = await page.evaluate(() => ({
        href: location.href,
        dialogs: document.querySelectorAll("dialog[open], [role=dialog]:not([hidden])").length,
        details: document.querySelectorAll("details[open]").length,
        mut: window.__mut || 0,
      }));
    } catch {
      after = { href: page.url(), dialogs: 0, details: 0, mut: 1 };
    }
    const bad = responses.filter((r) => r.status >= 400);
    let outcome;
    if (bad.length) outcome = "http-error";
    else if (dl) outcome = "download";
    else if (after.href !== before.href) outcome = "navigated";
    else if (after.dialogs > before.dialogs) outcome = "dialog";
    else if (after.details > before.details) outcome = "menu";
    else if (responses.length && after.mut) outcome = "updated";
    else if (after.mut) outcome = "ui-change";
    else if (control.href && new URL(control.href, BASE + url).href === new URL(BASE + url).href)
      outcome = "same-page"; // a link to the page we are on (e.g. the active nav item)
    else outcome = "no-effect";
    if (outcome === "menu") {
      result.children = await page.evaluate(listControls, null).then((all) =>
        all.filter((c) => c.sel.startsWith(control.sel.replace(/ > summary:nth-of-type\(\d+\)$/, "")) && c.sel !== control.sel)
      );
    }
    return { ...result, outcome, http: bad.slice(0, 3), errors: errors.slice(0, 3) };
  } catch (e) {
    return { ...result, outcome: "probe-error", detail: String(e.message).split("\n")[0].slice(0, 200) };
  } finally {
    await ctx.close();
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const pages = await discover(ctx);
  console.log(`pages: ${pages.length}`);
  const results = [];
  const sharedDone = new Set();
  for (const { url, status } of pages) {
    if (status !== 200) {
      results.push({ url, outcome: "page-error", detail: `HTTP ${status}` });
      continue;
    }
    const p = await ctx.newPage();
    await p.goto(BASE + url, { waitUntil: "domcontentloaded" });
    await p.waitForTimeout(200);
    const controls = (await p.evaluate(listControls, null)).slice(0, MAX_CONTROLS);
    await p.close();
    let n = 0;
    for (const c of controls) {
      const key = `${c.text}|${c.tag}`;
      if (c.shared) {
        if (sharedDone.has(key)) continue;
        sharedDone.add(key);
      }
      if (SKIP_TEXT.test(c.text)) continue;
      if (c.disabled) {
        results.push({ url, sel: c.sel, text: c.text, tag: c.tag, outcome: c.reason ? "disabled-with-reason" : "disabled-no-reason", detail: c.reason });
        continue;
      }
      const r = await probe(browser, url, c, null);
      results.push(r);
      n++;
      for (const child of r.children || []) {
        if (child.disabled) {
          results.push({ url, sel: child.sel, text: child.text, tag: child.tag, outcome: child.reason ? "disabled-with-reason" : "disabled-no-reason", detail: child.reason, via: c.text });
          continue;
        }
        results.push({ ...(await probe(browser, url, child, c.sel)), via: c.text });
      }
    }
    console.log(`${url}: ${controls.length} controls, ${n} probed`);
  }
  await browser.close();

  const PROBLEMS = new Set(["no-effect", "http-error", "blocked-by-overlay", "click-error", "probe-error", "page-error", "disabled-no-reason"]);
  const problems = results.filter((r) => PROBLEMS.has(r.outcome) || (r.errors && r.errors.length));
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify({ base: BASE, pages, results }, null, 2));
  const count = {};
  for (const r of results) count[r.outcome] = (count[r.outcome] || 0) + 1;
  const lines = [
    `# UX audit — ${new Date().toISOString()}`,
    "",
    `Base: ${BASE} · pages: ${pages.length} · controls probed: ${results.length}`,
    "",
    "| Outcome | Count |",
    "| --- | --- |",
    ...Object.entries(count).sort((a, b) => b[1] - a[1]).map(([k, v]) => `| ${k} | ${v} |`),
    "",
    `## Problems (${problems.length})`,
    "",
    "| Page | Control | Outcome | Detail |",
    "| --- | --- | --- | --- |",
    ...problems.map((r) =>
      `| ${r.url} | ${(r.via ? r.via + " › " : "") + (r.text || r.sel || "").replace(/\|/g, "/")} | ${r.outcome} | ${[r.detail, ...(r.http || []).map((h) => `${h.status} ${h.url}`), ...(r.errors || [])].filter(Boolean).join("; ").replace(/\|/g, "/").slice(0, 220)} |`
    ),
  ];
  fs.writeFileSync(path.join(OUT, "report.md"), lines.join("\n") + "\n");
  console.log(`done: ${results.length} controls, ${problems.length} problems → ${OUT}/report.md`);
  process.exit(problems.length ? 1 : 0);
})();
