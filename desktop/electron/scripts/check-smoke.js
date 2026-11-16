"use strict";
// Read the result.json written by `Pépite --smoke-test=<dir>` and fail when it is not ok.
const fs = require("node:fs");
const path = require("node:path");

const dir = process.argv[2];
const file = path.join(dir, "result.json");
if (!fs.existsSync(file)) {
  console.error(`No ${file}: the app did not finish its smoke test.`);
  process.exit(1);
}
const result = JSON.parse(fs.readFileSync(file, "utf8"));
for (const page of result.pages || []) console.log(`ok   ${page.name}: ${page.title} (desktop mode: ${page.desktopMode})`);
if (!result.ok) {
  console.error(`FAIL ${result.error}`);
  process.exit(1);
}
if (!result.menu) {
  console.error("FAIL no application menu");
  process.exit(1);
}
console.log(`Desktop app ${result.version} on ${result.platform}: smoke test passed.`);
