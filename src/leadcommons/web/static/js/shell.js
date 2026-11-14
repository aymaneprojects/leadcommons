"use strict";
/* Shell behaviour: command palette (Ctrl/Cmd+K), notifications, collapsed-rail tooltips,
   create-list dialog and list filtering. Loaded after app.js. The old palette in app.js looks for
   #palette, which no longer exists, so it does nothing; this file owns the new one. */
(function () {
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function lsGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function lsSet(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* unavailable */ } }

  /* ---------------------------------------------------------------- shortcut label */
  var isMac = /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent || "");
  $$("[data-kbd]").forEach(function (el) { el.textContent = isMac ? "⌘ K" : "Ctrl K"; });

  /* ---------------------------------------------------------------- command palette */
  var pal = $("#lc-palette"), pin = $("#lc-palette-input"), plist = $("#lc-palette-list");
  var palIdx = 0, palClosedAt = 0;

  function visible(el) { return !el.hidden && !el.closest("[hidden]"); }
  function palItems() { return $$(".pal-item", plist).filter(visible); }

  function filterStatic() {
    var q = pin.value.trim(), ql = q.toLowerCase();
    $$(".pal-item", plist).forEach(function (a) {
      if (a.hasAttribute("data-dyn")) {
        var kind = a.getAttribute("data-dyn");
        a.hidden = !q;
        if (q) {
          $(".pal-label", a).textContent = (kind === "companies" ? "Search companies for “" : "Search people for “") + q + "”";
          a.setAttribute("href", "/" + kind + "?q=" + encodeURIComponent(q));
        }
      } else if (a.closest("[data-pal-group]")) {
        var hay = ($(".pal-label", a).textContent + " " + (a.getAttribute("data-kw") || "")).toLowerCase();
        a.hidden = q ? hay.indexOf(ql) < 0 : !a.hasAttribute("data-top");
      }
    });
    $$("[data-pal-group]", plist).forEach(function (g) {
      g.hidden = !$$(".pal-item", g).some(function (a) { return !a.hidden; });
    });
  }

  function select(i, scroll) {
    var items = palItems();
    if (!items.length) { palIdx = 0; return; }
    palIdx = (i + items.length) % items.length;
    $$(".pal-item.on", plist).forEach(function (a) { a.classList.remove("on"); a.removeAttribute("aria-selected"); });
    var cur = items[palIdx];
    cur.classList.add("on");
    cur.setAttribute("aria-selected", "true");
    pin.setAttribute("aria-activedescendant", cur.id || "");
    if (scroll !== false) cur.scrollIntoView({ block: "nearest" });
  }

  var fetchEl = $("#lc-palette-fetch"), fetchTimer = null;
  function fetchRemote(now) {
    if (!fetchEl || !window.htmx) return;
    clearTimeout(fetchTimer);
    if (now) window.htmx.trigger(fetchEl, "palette-fetch");
    else fetchTimer = setTimeout(function () { window.htmx.trigger(fetchEl, "palette-fetch"); }, 150);
  }

  function openPalette() {
    if (!pal || !pal.showModal) return;
    if (!pal.open) pal.showModal();
    pin.value = "";
    filterStatic();
    select(0);
    pin.focus();
    fetchRemote(true);
  }
  function closePalette() { if (pal && pal.open) pal.close(); }

  if (pal && pin) {
    pin.addEventListener("input", function () { filterStatic(); select(0, false); fetchRemote(false); });
    pin.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); select(palIdx + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); select(palIdx - 1); }
      else if (e.key === "Home" && e.ctrlKey) { e.preventDefault(); select(0); }
      else if (e.key === "Enter") {
        e.preventDefault();
        var cur = palItems()[palIdx];
        if (cur) cur.click();
        else if (pin.value.trim()) location.href = "/companies?q=" + encodeURIComponent(pin.value.trim());
      }
    });
    plist.addEventListener("click", function (e) {
      var a = e.target.closest(".pal-item");
      if (!a) return;
      if (a.getAttribute("href") === "#") e.preventDefault();
      closePalette();
    });
    plist.addEventListener("mousemove", function (e) {
      var a = e.target.closest(".pal-item");
      if (!a || a.classList.contains("on")) return;
      var i = palItems().indexOf(a);
      if (i >= 0) select(i, false);
    });
    pal.addEventListener("click", function (e) { if (e.target === pal) closePalette(); });
    pal.addEventListener("close", function () {
      palClosedAt = Date.now();
      var ts = $("#shell-topsearch"); if (ts && doc.activeElement === ts) ts.blur();
    });
    doc.addEventListener("htmx:afterSwap", function (e) {
      if (e.target && e.target.id === "lc-palette-remote") {
        $$(".pal-item", e.target).forEach(function (a, i) { if (!a.id) a.id = "pal-r-" + i; });
        select(palIdx, false);
      }
    });
  }

  // Ctrl/Cmd+K anywhere. Capture phase so it wins over any older handler.
  doc.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (pal && pal.open) closePalette(); else openPalette();
    }
  }, true);

  // The top bar field is a real search form without JS; with JS it opens the palette.
  var ts = $("#shell-topsearch");
  if (ts) {
    ts.addEventListener("focus", function () {
      if (Date.now() - palClosedAt < 250) return;
      ts.blur(); openPalette();
    });
    ts.addEventListener("click", function () {
      if (Date.now() - palClosedAt < 250) return;
      ts.blur(); openPalette();
    });
    ts.closest("form").addEventListener("submit", function (e) { e.preventDefault(); openPalette(); });
  }

  /* ---------------------------------------------------------------- notifications */
  var nmenu = $("#notif-menu"), nlist = $("#notif-list"), nbadge = $("[data-notif-badge]"), nbtn = $("[data-notif-toggle]");
  function readSet() {
    try { return JSON.parse(lsGet("lc-notif-read") || "[]"); } catch (e) { return []; }
  }
  function writeSet(ids) { lsSet("lc-notif-read", JSON.stringify(ids.slice(-300))); }
  function refreshNotifs() {
    if (!nlist) return;
    var read = readSet(), unread = 0;
    $$("[data-notif-id]", nlist).forEach(function (row) {
      var isRead = read.indexOf(row.getAttribute("data-notif-id")) >= 0;
      row.classList.toggle("unread", !isRead);
      if (!isRead) unread++;
    });
    if (nbadge) nbadge.hidden = unread === 0;
    if (nbtn) nbtn.setAttribute("aria-label", unread ? "Notifications, " + unread + " unread" : "Notifications");
    var all = $("[data-notif-readall]");
    if (all) all.disabled = unread === 0;
  }
  function notifOpen(on) {
    if (!nmenu) return;
    nmenu.hidden = !on;
    nbtn.setAttribute("aria-expanded", String(on));
    if (on) { var jm = $("#jobs-menu"); if (jm) jm.hidden = true; }
  }
  if (nlist) {
    doc.addEventListener("htmx:afterSwap", function (e) { if (e.target === nlist) refreshNotifs(); });
    refreshNotifs();
  }
  doc.addEventListener("click", function (e) {
    var t = e.target;
    if (!t.closest) return;
    if (t.closest("[data-notif-toggle]")) { notifOpen(nmenu.hidden); return; }
    if (t.closest("[data-notif-close]")) { notifOpen(false); nbtn.focus(); return; }
    if (t.closest("[data-notif-readall]")) {
      var ids = readSet();
      $$("[data-notif-id]", nlist).forEach(function (row) {
        var id = row.getAttribute("data-notif-id");
        if (ids.indexOf(id) < 0) ids.push(id);
      });
      writeSet(ids); refreshNotifs(); return;
    }
    if (nmenu && !nmenu.hidden && !t.closest(".notif")) notifOpen(false);
    if (t.closest("[data-jobs-toggle]") && nmenu) notifOpen(false);
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && nmenu && !nmenu.hidden) { notifOpen(false); nbtn.focus(); }
  });

  /* ---------------------------------------------------------------- collapsed rail tooltips */
  var tip = $("#nav-tip");
  function tipTarget(e) {
    var el = e.target.closest ? e.target.closest(".sidebar [data-tip]") : null;
    if (!el || !doc.documentElement.classList.contains("nav-collapsed") || el.closest(".settings-mode")) return null;
    return el;
  }
  function showTip(e) {
    var el = tipTarget(e);
    if (!el || !tip) return;
    var r = el.getBoundingClientRect();
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    tip.style.left = (r.right + 8) + "px";
    tip.style.top = (r.top + r.height / 2 - tip.offsetHeight / 2) + "px";
  }
  function hideTip() { if (tip) tip.hidden = true; }
  doc.addEventListener("mouseover", showTip);
  doc.addEventListener("focusin", showTip);
  doc.addEventListener("mouseout", hideTip);
  doc.addEventListener("focusout", hideTip);
  doc.addEventListener("click", hideTip);

  /* ---------------------------------------------------------------- create list dialog */
  var cl = $("#create-list");
  function openCreateList(kind) {
    if (!cl || !cl.showModal) return;
    var sel = $("#cl-kind");
    if (sel && (kind === "people" || kind === "companies")) sel.value = kind;
    if (!cl.open) cl.showModal();
    var name = $("#cl-name"); if (name) name.focus();
  }
  doc.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-create-list]") : null;
    if (b) { openCreateList(b.getAttribute("data-create-list")); return; }
    if (e.target === cl) cl.close();
  });
  if (cl && /[?&]create=/.test(location.search)) {
    var m = location.search.match(/[?&]create=(people|companies)/);
    openCreateList(m ? m[1] : "");
  }

  /* ---------------------------------------------------------------- filter a list's rows */
  var tf = $("[data-table-filter]");
  if (tf) {
    var table = $(tf.getAttribute("data-table-filter"));
    var none = $("[data-filter-none]");
    tf.addEventListener("input", function () {
      var q = tf.value.trim().toLowerCase(), shown = 0;
      $$("tbody tr", table).forEach(function (tr) {
        var hit = !q || tr.textContent.toLowerCase().indexOf(q) >= 0;
        tr.hidden = !hit;
        if (hit) shown++;
        else { var cb = $("input.rowcb", tr); if (cb && cb.checked) { cb.checked = false; } }
      });
      if (none) none.hidden = shown > 0;
      var first = $("input.rowcb", table);
      if (first) first.dispatchEvent(new Event("change", { bubbles: true }));
    });
    // "Select all" must only select the rows that are visible.
    doc.addEventListener("change", function (e) {
      var t = e.target;
      if (!t.classList || !t.classList.contains("allcb")) return;
      $$("tbody tr", table).forEach(function (tr) {
        var cb = $("input.rowcb", tr);
        if (cb) cb.checked = t.checked && !tr.hidden;
      });
      var first = $("input.rowcb", table);
      if (first) first.dispatchEvent(new Event("change", { bubbles: true }));
    }, true);
  }
})();
