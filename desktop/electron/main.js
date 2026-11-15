"use strict";
// LeadCommons desktop app. Electron draws the window, menus, dialogs and notifications; the
// frozen Python backend (resources/backend) serves the screens on 127.0.0.1 only.

const {
  app, BrowserWindow, Menu, Notification, clipboard, dialog, nativeTheme, net, screen, session, shell,
} = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const L = require("./lib/logic");
const { stringsFor } = require("./lib/strings");

const REPO = "aymaneprojects/leadcommons";
const DOCS_URL = `https://github.com/${REPO}/blob/main/docs/desktop.md`;
const ISSUES_URL = `https://github.com/${REPO}/issues`;
const BG = "#121110";
const isMac = process.platform === "darwin";

const smokeArg = process.argv.find((a) => a.startsWith("--smoke-test"));
const SMOKE_DIR = smokeArg ? (smokeArg.split("=")[1] || path.join(os.tmpdir(), "leadcommons-smoke")) : null;
const SMOKE_HOLD = Number((process.argv.find((a) => a.startsWith("--smoke-hold=")) || "=0").split("=")[1]) || 0;

let S = stringsFor("en");
let backend = null;
let baseUrl = null;
let mainWindow = null;
let splash = null;
let quitting = false;
let jobsTimer = null;
const seenJobs = new Map();

const HOME = L.dataHome({ homedir: os.homedir() });
const LOG_FILE = path.join(HOME, "desktop.log");

// -- single instance: a second launch focuses the open window --------------------------------

if (!SMOKE_DIR && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(start).catch(fatal);
}

// -- start-up ------------------------------------------------------------------------------

async function start() {
  S = stringsFor(app.getLocale());
  nativeTheme.themeSource = "dark";
  app.setAboutPanelOptions({
    applicationName: "LeadCommons",
    applicationVersion: app.getVersion(),
    copyright: "LeadCommons contributors. AGPL-3.0-or-later.",
    website: `https://github.com/${REPO}`,
  });
  setupSession();
  showSplash();
  Menu.setApplicationMenu(buildMenu());

  const port = await L.freePort();
  baseUrl = `http://127.0.0.1:${port}/`;
  startBackend(port);
  await L.waitHealthy(baseUrl, { isDead: () => !backend || backend.exitCode !== null });
  createMainWindow();
  if (!SMOKE_DIR) {
    jobsTimer = setInterval(pollJobs, 4000);
    setTimeout(() => checkForUpdates(false), 15000);
  }
}

function startBackend(port) {
  const exe = L.backendExecutable({ resourcesPath: process.resourcesPath, platform: process.platform });
  const args = ["--headless", "--port", String(port), "--parent-pid", String(process.pid)];
  backend = spawn(exe, args, {
    env: { ...process.env, LEADCOMMONS_DESKTOP: "1" },
    stdio: "ignore",
    windowsHide: true,
  });
  backend.on("error", (err) => fatal(err));
  backend.on("exit", (code) => {
    if (!quitting) fatal(new Error(`backend exited with code ${code}`));
  });
}

function showSplash() {
  splash = new BrowserWindow({
    width: 420, height: 300, frame: false, resizable: false, movable: true, show: false,
    backgroundColor: BG, skipTaskbar: false, title: "LeadCommons",
    webPreferences: { contextIsolation: true, sandbox: true },
  });
  splash.loadFile(path.join(__dirname, "splash.html"), { query: { t: S.starting, h: S.startingHint } });
  splash.once("ready-to-show", () => splash && splash.show());
}

// -- the main window ---------------------------------------------------------------------------

function createMainWindow() {
  const state = loadWindowState();
  mainWindow = new BrowserWindow({
    ...state.bounds,
    minWidth: 1024, minHeight: 680, show: false, backgroundColor: BG, title: "LeadCommons",
    icon: isMac ? undefined : path.join(__dirname, "lib", "logo.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: true,
      devTools: !app.isPackaged,
    },
  });
  if (state.maximized) mainWindow.maximize();
  const wc = mainWindow.webContents;

  // Links: our own pages stay in the window, everything else opens in the system browser.
  wc.setWindowOpenHandler(({ url }) => {
    if (L.isLocal(url, baseUrl)) mainWindow.loadURL(url);
    else shell.openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (event, url) => {
    if (!L.isLocal(url, baseUrl)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
  // No browser shortcuts (reload, developer tools) in the installed app.
  wc.on("before-input-event", (event, input) => {
    if (!app.isPackaged || input.type !== "keyDown") return;
    const k = String(input.key).toLowerCase();
    const mod = input.control || input.meta;
    if (k === "f5" || k === "f12" || (mod && k === "r") || (mod && input.shift && k === "i")) event.preventDefault();
  });
  wc.on("context-menu", (_event, params) => showContextMenu(params));
  wc.on("render-process-gone", () => { if (!quitting) wc.reload(); });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (splash) { splash.destroy(); splash = null; }
  });
  mainWindow.on("close", () => saveWindowState());
  mainWindow.on("closed", () => { mainWindow = null; });

  mainWindow.loadURL(SMOKE_DIR ? baseUrl + "companies?all=1" : baseUrl);
  if (SMOKE_DIR) runSmokeTest().catch((err) => smokeDone(false, String(err)));
}

function showContextMenu(params) {
  const items = [];
  if (params.isEditable) {
    items.push({ label: S.cut, role: "cut", enabled: params.editFlags.canCut },
      { label: S.copy, role: "copy", enabled: params.editFlags.canCopy },
      { label: S.paste, role: "paste", enabled: params.editFlags.canPaste },
      { type: "separator" }, { label: S.selectAll, role: "selectAll" });
  } else if (params.selectionText && params.selectionText.trim()) {
    items.push({ label: S.copy, role: "copy" });
  }
  if (params.linkURL) {
    if (items.length) items.push({ type: "separator" });
    items.push({ label: S.copyLink, click: () => clipboard.writeText(params.linkURL) });
    if (!L.isLocal(params.linkURL, baseUrl)) items.push({ label: S.openInBrowser, click: () => shell.openExternal(params.linkURL) });
  }
  if (items.length) Menu.buildFromTemplate(items).popup({ window: mainWindow });
}

// -- downloads, permissions ---------------------------------------------------------------

function setupSession() {
  const ses = session.defaultSession;
  ses.on("will-download", (_event, item) => {
    item.setSaveDialogOptions({
      title: S.saveExport,
      defaultPath: path.join(app.getPath("downloads"), item.getFilename()),
    });
    item.once("done", (_e, state) => {
      if (state === "completed") notify(S.exportSaved, `${path.basename(item.getSavePath())}. ${S.showInFolder}`, () => shell.showItemInFolder(item.getSavePath()));
    });
  });
  const allowed = new Set(["clipboard-sanitized-write", "clipboard-read", "fullscreen"]);
  ses.setPermissionRequestHandler((_wc, permission, callback) => callback(allowed.has(permission)));
}

// -- menu ----------------------------------------------------------------------------------

function go(page) {
  return () => {
    if (!mainWindow) createMainWindow();
    else mainWindow.loadURL(baseUrl + page);
  };
}

function buildMenu() {
  const sep = { type: "separator" };
  const history = (dir) => () => {
    const h = mainWindow && mainWindow.webContents.navigationHistory;
    if (!h) return;
    if (dir < 0 && h.canGoBack()) h.goBack();
    if (dir > 0 && h.canGoForward()) h.goForward();
  };
  const pages = [
    [S.home, ""], [S.assistant, "assistant"], [S.companies, "companies"], [S.people, "people"],
    [S.lists, "lists"], [S.sequences, "sequences"], [S.emails, "emails"], [S.tasks, "tasks"], [S.deals, "deals"],
  ];
  const template = [
    ...(isMac ? [{
      label: "LeadCommons",
      submenu: [{ role: "about", label: S.about }, sep,
        { label: S.settings, accelerator: "Cmd+,", click: go("settings") }, sep,
        { role: "services" }, sep, { role: "hide" }, { role: "hideOthers" }, { role: "unhide" }, sep,
        { role: "quit", label: S.quit }],
    }] : []),
    {
      label: S.file,
      submenu: [
        { label: S.newCompanySearch, accelerator: "CmdOrCtrl+N", click: go("companies") },
        { label: S.newPeopleSearch, accelerator: "CmdOrCtrl+Shift+N", click: go("people") },
        sep,
        { label: S.importCsv, accelerator: "CmdOrCtrl+O", click: go("enrich") },
        { label: S.updateData, click: go("settings/data-packs") },
        ...(isMac ? [] : [sep, { label: S.settings, accelerator: "Ctrl+,", click: go("settings") }]),
        sep,
        isMac ? { role: "close", label: S.close } : { role: "quit", label: S.quit, accelerator: "Ctrl+Q" },
      ],
    },
    {
      label: S.edit,
      submenu: [{ role: "undo", label: S.undo }, { role: "redo", label: S.redo }, sep,
        { role: "cut", label: S.cut }, { role: "copy", label: S.copy }, { role: "paste", label: S.paste }, sep,
        { role: "selectAll", label: S.selectAll }],
    },
    {
      label: S.view,
      submenu: [{ role: "resetZoom", label: S.actualSize }, { role: "zoomIn", label: S.zoomIn },
        { role: "zoomOut", label: S.zoomOut }, sep, { role: "togglefullscreen", label: S.fullscreen },
        ...(app.isPackaged ? [] : [sep, { role: "reload" }, { role: "toggleDevTools" }])],
    },
    {
      label: S.go,
      submenu: [
        { label: S.back, accelerator: isMac ? "Cmd+[" : "Alt+Left", click: history(-1) },
        { label: S.forward, accelerator: isMac ? "Cmd+]" : "Alt+Right", click: history(1) },
        sep,
        ...pages.map(([label, page], i) => ({ label, accelerator: `CmdOrCtrl+${i + 1}`, click: go(page) })),
      ],
    },
    {
      label: S.window,
      submenu: isMac
        ? [{ role: "minimize", label: S.minimize }, { role: "zoom" }, sep, { role: "front" }]
        : [{ role: "minimize", label: S.minimize }, { role: "close", label: S.close }],
    },
    {
      role: "help",
      label: S.help,
      submenu: [
        { label: S.documentation, click: () => shell.openExternal(DOCS_URL) },
        { label: S.reportProblem, click: () => shell.openExternal(ISSUES_URL) },
        sep,
        { label: S.openDataFolder, click: () => shell.openPath(HOME) },
        { label: S.openLog, click: () => shell.openPath(LOG_FILE) },
        sep,
        { label: S.checkUpdates, click: () => checkForUpdates(true) },
        ...(isMac ? [] : [{ label: S.about, click: showAbout }]),
      ],
    },
  ];
  return Menu.buildFromTemplate(template);
}

function showAbout() {
  dialog.showMessageBox(mainWindow, {
    type: "info", title: S.about, message: "LeadCommons", detail: S.aboutDetail(app.getVersion()),
    icon: path.join(__dirname, "lib", "logo.png"), buttons: ["OK"],
  });
}

// -- background jobs: system notifications and the dock / taskbar badge -------------------

async function pollJobs() {
  if (!baseUrl) return;
  try {
    const { status, body } = await L.getText(baseUrl + "api/jobs?limit=30");
    if (status !== 200) return;
    const { finished, running } = L.jobChanges(seenJobs, JSON.parse(body).items);
    if (isMac || process.platform === "linux") app.setBadgeCount(running);
    if (mainWindow && process.platform === "win32") mainWindow.setProgressBar(running > 0 ? 2 : -1);
    const away = !mainWindow || !mainWindow.isFocused();
    for (const job of finished) {
      if (away) notify(job.label, job.status === "done" ? S.jobDone : `${S.jobFailed}: ${job.error || ""}`, focusWindow);
    }
  } catch (_) { /* backend busy or restarting: try again next tick */ }
}

function notify(title, body, onClick) {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body, silent: false });
  if (onClick) n.on("click", onClick);
  n.show();
}

function focusWindow() {
  if (!mainWindow) return createMainWindow();
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

// -- updates: compare with the newest GitHub release ---------------------------------------

async function checkForUpdates(verbose) {
  try {
    const res = await net.fetch(`https://api.github.com/repos/${REPO}/releases?per_page=20`, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "LeadCommons-Desktop" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const latest = L.latestAppRelease(await res.json());
    if (latest && L.compareVersions(latest.tag_name, app.getVersion()) > 0) {
      const { response } = await dialog.showMessageBox(mainWindow, {
        type: "info", message: S.updateAvailable(latest.tag_name.replace(/^v/, "")),
        detail: S.updateDetail(app.getVersion()), buttons: [S.download, S.later], defaultId: 0, cancelId: 1,
      });
      if (response === 0) shell.openExternal(latest.html_url);
    } else if (verbose) {
      dialog.showMessageBox(mainWindow, { type: "info", message: S.upToDate, detail: S.upToDateDetail(app.getVersion()) });
    }
  } catch (err) {
    if (verbose) dialog.showMessageBox(mainWindow, { type: "warning", message: S.updateError, detail: String(err.message || err) });
  }
}

// -- window size and position, kept between launches ----------------------------------------

function stateFile() {
  return path.join(app.getPath("userData"), "window-state.json");
}

function loadWindowState() {
  const fallback = { bounds: { width: 1440, height: 900 }, maximized: false };
  try {
    const saved = JSON.parse(fs.readFileSync(stateFile(), "utf8"));
    const b = saved.bounds || {};
    const visible = screen.getAllDisplays().some(({ workArea: w }) =>
      b.x >= w.x - 50 && b.y >= w.y - 50 && b.x + 200 <= w.x + w.width && b.y + 100 <= w.y + w.height);
    if (!visible || !(b.width >= 1024 && b.height >= 680)) return fallback;
    return { bounds: b, maximized: Boolean(saved.maximized) };
  } catch (_) {
    return fallback;
  }
}

function saveWindowState() {
  if (!mainWindow || SMOKE_DIR) return;
  try {
    const maximized = mainWindow.isMaximized();
    const bounds = maximized ? mainWindow.getNormalBounds() : mainWindow.getBounds();
    fs.mkdirSync(path.dirname(stateFile()), { recursive: true });
    fs.writeFileSync(stateFile(), JSON.stringify({ bounds, maximized }));
  } catch (_) { /* not important */ }
}

// -- lifecycle -------------------------------------------------------------------------------

app.on("window-all-closed", () => {
  if (!isMac || SMOKE_DIR) app.quit(); // macOS apps stay in the Dock until Cmd+Q
});
app.on("activate", () => { if (baseUrl && !mainWindow) createMainWindow(); });
app.on("before-quit", () => {
  quitting = true;
  saveWindowState();
  if (jobsTimer) clearInterval(jobsTimer);
  // The backend also stops on its own when this process ends (it watches our pid).
  if (backend && backend.exitCode === null && process.platform !== "win32") backend.kill("SIGINT");
});

function fatal(err) {
  if (quitting) return;
  quitting = true;
  console.error(err);
  if (SMOKE_DIR) return smokeDone(false, String(err && err.stack ? err.stack : err));
  dialog.showErrorBox(S.backendFailed, `${S.backendFailedDetail(LOG_FILE)}\n\n${err && err.message ? err.message : err}`);
  if (backend && backend.exitCode === null) backend.kill();
  app.exit(1);
}

// -- smoke test (CI): load real screens, save what the window shows, then quit --------------

function waitLoad(wc) {
  return new Promise((resolve, reject) => {
    wc.once("did-finish-load", resolve);
    wc.once("did-fail-load", (_e, code, desc) => reject(new Error(`load failed: ${code} ${desc}`)));
  });
}

async function runSmokeTest() {
  fs.mkdirSync(SMOKE_DIR, { recursive: true });
  const wc = mainWindow.webContents;
  const pages = [];
  await waitLoad(wc);
  for (const [name, url] of [["companies", null], ["data-packs", "settings/data-packs"], ["home", ""]]) {
    if (url !== null) { const loaded = waitLoad(wc); wc.loadURL(baseUrl + url); await loaded; }
    await new Promise((r) => setTimeout(r, 1500));
    const image = await wc.capturePage();
    fs.writeFileSync(path.join(SMOKE_DIR, `${name}.png`), image.toPNG());
    const title = wc.getTitle();
    const isDesktop = await wc.executeJavaScript("document.documentElement.dataset.app === 'desktop'");
    pages.push({ name, title, url: wc.getURL(), desktopMode: isDesktop });
    if (!title.includes("LeadCommons") || !isDesktop) throw new Error(`unexpected page ${name}: ${title}`);
  }
  if (SMOKE_HOLD) await new Promise((r) => setTimeout(r, SMOKE_HOLD * 1000));
  smokeDone(true, null, pages);
}

function smokeDone(ok, error, pages = []) {
  try {
    fs.mkdirSync(SMOKE_DIR, { recursive: true });
    fs.writeFileSync(path.join(SMOKE_DIR, "result.json"), JSON.stringify({
      ok, error, pages, version: app.getVersion(), platform: process.platform, menu: Boolean(Menu.getApplicationMenu()),
    }, null, 2));
  } catch (_) { /* nothing else to do */ }
  quitting = true;
  if (backend && backend.exitCode === null) backend.kill();
  app.exit(ok ? 0 : 1);
}
