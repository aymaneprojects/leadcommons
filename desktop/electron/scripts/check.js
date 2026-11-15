"use strict";
// `npm run check` on every system: syntax-check the shell's files, then run its tests.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const js = (dir) => fs.readdirSync(path.join(root, dir)).filter((f) => f.endsWith(".js")).map((f) => path.join(dir, f));
for (const file of ["main.js", "preload.js", ...js("lib"), ...js("scripts")]) {
  execFileSync(process.execPath, ["--check", path.join(root, file)], { stdio: "inherit" });
}
const tests = fs.readdirSync(path.join(root, "test")).filter((f) => f.endsWith(".test.js")).map((f) => path.join(root, "test", f));
execFileSync(process.execPath, ["--test", ...tests], { stdio: "inherit" });
