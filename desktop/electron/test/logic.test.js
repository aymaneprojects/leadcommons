"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const path = require("node:path");
const L = require("../lib/logic");
const { stringsFor } = require("../lib/strings");

test("backend path inside the installed app", () => {
  assert.equal(L.backendExecutable({ resourcesPath: "/r", platform: "linux", env: {} }), path.join("/r", "backend", "leadcommons-backend"));
  assert.match(L.backendExecutable({ resourcesPath: "C:\\r", platform: "win32", env: {} }), /leadcommons-backend\.exe$/);
  assert.equal(L.backendExecutable({ resourcesPath: "/r", platform: "linux", env: { LEADCOMMONS_BACKEND: "/x" } }), "/x");
});

test("data folder is shared with the command line", () => {
  assert.equal(L.dataHome({ env: {}, homedir: "/home/a" }), path.join("/home/a", ".leadcommons"));
  assert.equal(L.dataHome({ env: { LEADCOMMONS_HOME: "/data" }, homedir: "/home/a" }), "/data");
});

test("only our own pages stay in the window", () => {
  const base = "http://127.0.0.1:5000/";
  assert.ok(L.isLocal("http://127.0.0.1:5000/companies?all=1", base));
  assert.ok(!L.isLocal("http://127.0.0.1:6000/", base));
  assert.ok(!L.isLocal("https://github.com/x", base));
  assert.ok(!L.isLocal("not a url", base));
});

test("versions compare numerically", () => {
  assert.ok(L.compareVersions("v0.1.10", "0.1.9") > 0);
  assert.equal(L.compareVersions("v1.2.0", "1.2.0"), 0);
  assert.ok(L.compareVersions("0.1.1", "0.2.0") < 0);
});

test("update check ignores data-pack and draft releases", () => {
  const latest = L.latestAppRelease([
    { tag_name: "packs-2026-12" },
    { tag_name: "v0.3.0", draft: true },
    { tag_name: "v0.1.1" },
    { tag_name: "v0.2.0" },
    { tag_name: "v0.2.1-rc1", prerelease: true },
  ]);
  assert.equal(latest.tag_name, "v0.2.0");
  assert.equal(L.latestAppRelease([{ tag_name: "packs-2026-11" }]), null);
});

test("job changes report only jobs that finished since the last poll", () => {
  const seen = new Map();
  let r = L.jobChanges(seen, [{ id: "a", status: "running" }, { id: "b", status: "done" }]);
  assert.equal(r.running, 1);
  assert.deepEqual(r.finished, []); // b was already done when first seen
  r = L.jobChanges(seen, [{ id: "a", status: "done" }, { id: "b", status: "done" }]);
  assert.deepEqual(r.finished.map((j) => j.id), ["a"]);
  assert.equal(r.running, 0);
});

test("free port and health wait against a tiny server", async () => {
  const port = await L.freePort();
  const srv = http.createServer((req, res) => res.end(req.url === "/healthz" ? '{"status":"ok"}' : "no"));
  await new Promise((r) => srv.listen(port, "127.0.0.1", r));
  try {
    await L.waitHealthy(`http://127.0.0.1:${port}/`, { timeoutMs: 3000 });
  } finally {
    srv.close();
  }
  await assert.rejects(L.waitHealthy(`http://127.0.0.1:${port}/`, { timeoutMs: 600, intervalMs: 100 }));
  await assert.rejects(L.waitHealthy(`http://127.0.0.1:${port}/`, { timeoutMs: 5000, isDead: () => true }), /stopped/);
});

test("French or English words", () => {
  assert.equal(stringsFor("fr-FR").file, "Fichier");
  assert.equal(stringsFor("en-US").file, "File");
  assert.equal(stringsFor(undefined).file, "File");
  for (const key of Object.keys(stringsFor("en"))) assert.ok(key in stringsFor("fr"), key);
});
