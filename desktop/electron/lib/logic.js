"use strict";
// Plain logic of the desktop shell, kept free of Electron so `node --test` can check it.

const path = require("node:path");
const net = require("node:net");
const http = require("node:http");

/** Where the frozen Python backend lives inside the installed app (or PEPITE_BACKEND). */
function backendExecutable({ resourcesPath, platform, env = process.env }) {
  if (env.PEPITE_BACKEND) return env.PEPITE_BACKEND;
  const exe = platform === "win32" ? "pepite-backend.exe" : "pepite-backend";
  return path.join(resourcesPath, "backend", exe);
}

/** The data folder shared with the command-line install (~/.pepite or PEPITE_HOME). */
function dataHome({ env = process.env, homedir }) {
  return env.PEPITE_HOME || path.join(homedir, ".pepite");
}

/** A free TCP port on 127.0.0.1. */
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

/** GET url and resolve with { status, body } (local backend only, short timeout). */
function getText(url, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { body += c; });
      res.on("end", () => resolve({ status: res.statusCode, body }));
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

/** Resolve once the backend answers /healthz, reject after timeoutMs or when isDead() says so. */
async function waitHealthy(baseUrl, { timeoutMs = 90000, intervalMs = 250, isDead = () => false } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isDead()) throw new Error("the background service stopped while starting");
    try {
      const { status, body } = await getText(baseUrl + "healthz", 2000);
      if (status === 200 && body.includes('"ok"')) return;
    } catch (_) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("the background service did not start in time");
}

/** True for pages of our own backend; everything else opens in the system browser. */
function isLocal(url, baseUrl) {
  try {
    const u = new URL(url);
    const b = new URL(baseUrl);
    return u.protocol === b.protocol && u.host === b.host;
  } catch (_) {
    return false;
  }
}

/** Compare "1.2.10" and "1.2.9": >0 when a is newer. Ignores a leading "v" and suffixes. */
function compareVersions(a, b) {
  const parse = (v) => String(v).replace(/^v/, "").split(/[.+-]/).slice(0, 3).map((n) => parseInt(n, 10) || 0);
  const [x, y] = [parse(a), parse(b)];
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
  return 0;
}

/** The newest app release (tags v1.2.3; data-pack releases packs-YYYY-MM are skipped). */
function latestAppRelease(releases) {
  const apps = (releases || []).filter((r) => /^v\d/.test(r.tag_name || "") && !r.draft && !r.prerelease);
  apps.sort((p, q) => compareVersions(q.tag_name, p.tag_name));
  return apps[0] || null;
}

/**
 * Compare the job list with the previous poll. Returns jobs that just finished (they were seen
 * queued or running before) and the number still running. `seen` maps id -> last status.
 */
function jobChanges(seen, items) {
  const finished = [];
  let running = 0;
  for (const job of items || []) {
    const before = seen.get(job.id);
    if (job.status === "queued" || job.status === "running") running += 1;
    if ((before === "queued" || before === "running") && (job.status === "done" || job.status === "failed")) {
      finished.push(job);
    }
    seen.set(job.id, job.status);
  }
  return { finished, running };
}

module.exports = {
  backendExecutable,
  dataHome,
  freePort,
  getText,
  waitHealthy,
  isLocal,
  compareVersions,
  latestAppRelease,
  jobChanges,
};
