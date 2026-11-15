/* Search screens: column chooser, filter panel + All filters window, tag inputs, selection
   helpers, preferences, profile tabs. Loaded after app.js (which owns row selection, the drawer
   and toasts). Everything degrades to plain forms and links without JavaScript. */
"use strict";
(function () {
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function lsGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function lsSet(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* storage unavailable */ } }
  function lsDel(key) { try { localStorage.removeItem(key); } catch (e) { /* storage unavailable */ } }
  function lsJson(key) {
    var raw = lsGet(key);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (e) { return null; }
  }
  function fire(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }
  function toast(message) {
    doc.body.dispatchEvent(new CustomEvent("toast", { detail: { message: message }, bubbles: true }));
  }
  function kind() { var w = $("#workspace"); return w ? w.getAttribute("data-kind") : ""; }

  /* ---------- preferences: compact rows, source chips, results per page ---------- */
  var root = doc.documentElement;
  function applyPrefs() {
    root.classList.toggle("compact", lsGet("lc-pref-compact") === "1");
    root.classList.toggle("no-src", lsGet("lc-pref-src") === "0");
    $$("[data-pref]").forEach(function (box) {
      var key = box.getAttribute("data-pref");
      box.checked = key === "compact" ? lsGet("lc-pref-compact") === "1" : lsGet("lc-pref-src") !== "0";
    });
  }
  doc.addEventListener("change", function (e) {
    var t = e.target;
    if (t.matches && t.matches("[data-pref]")) {
      lsSet("lc-pref-" + t.getAttribute("data-pref"), t.checked ? "1" : "0");
      applyPrefs();
    } else if (t.matches && t.matches("select[name=per]")) {
      lsSet("lc-per", t.value);
    }
  });
  function restorePerPage() {
    var per = lsGet("lc-per");
    if (!per || per === "25" || !/^\/(companies|people)\/?$/.test(location.pathname)) return;
    var q = new URLSearchParams(location.search);
    if (q.has("per") || (per !== "50" && per !== "100")) return;
    q.set("per", per);
    location.replace(location.pathname + "?" + q.toString());
  }

  /* ---------- columns + Search settings drawer ----------
     The column state is an ordered list of visible keys in localStorage (data-key of the
     drawer). "name" is locked first; the row actions column is locked last. */
  function chooser() { return $("#col-chooser"); }
  function fieldData() {
    var c = chooser(), el = c && $("[data-fld-data]", c);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }
  var ICON_SPRITE = "/static/icons/results-sprite.svg#r-";
  function svg(name, cls) {
    return '<svg class="icon ' + (cls || "") + '" aria-hidden="true" focusable="false"><use href="' + ICON_SPRITE + name + '"></use></svg>';
  }
  function colOrder() {
    var d = fieldData(); if (!d) return null;
    var known = {}, locked = [];
    d.columns.forEach(function (c) { known[c.key] = c; if (c.locked) locked.push(c.key); });
    var saved = lsJson(chooser().getAttribute("data-key"));
    var keys = Array.isArray(saved) ? saved.filter(function (k) { return known[k]; })
      : d.columns.filter(function (c) { return c.default; }).map(function (c) { return c.key; });
    keys = keys.filter(function (k) { return locked.indexOf(k) < 0; });
    return locked.concat(keys.filter(function (k, i) { return keys.indexOf(k) === i; }));
  }
  function applyCols() {
    var order = colOrder(); if (!order) return;
    $$("table[data-cols] tr").forEach(function (tr) {
      var cells = {}, tail = $("th.addcol, td.ract", tr);
      $$("[data-col]", tr).forEach(function (cell) { cells[cell.getAttribute("data-col")] = cell; });
      order.forEach(function (k) { var cell = cells[k]; if (cell) { cell.hidden = false; tr.insertBefore(cell, tail); delete cells[k]; } });
      Object.keys(cells).forEach(function (k) { cells[k].hidden = true; tr.insertBefore(cells[k], tail); });
    });
    $$("[data-col-count]").forEach(function (el) { el.textContent = String(order.length); });
    renderFields(order);
  }
  function saveOrder(order, said) {
    var c = chooser(); if (!c) return;
    var locked = (fieldData().columns || []).filter(function (x) { return x.locked; }).map(function (x) { return x.key; });
    lsSet(c.getAttribute("data-key"), JSON.stringify(order.filter(function (k) { return locked.indexOf(k) < 0; })));
    applyCols();
    var live = $("[data-fld-live]", c); if (live && said) live.textContent = said;
  }
  function labelOf(key) {
    var d = fieldData(), hit = d && d.columns.filter(function (c) { return c.key === key; })[0];
    return hit ? hit.label : key;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]; }); }
  function renderFields(order) {
    var c = chooser(), list = c && $("[data-fld-list]", c), d = fieldData();
    if (!list || !d) return;
    var icons = d.icons || {};
    var swappable = d.columns.filter(function (x) { return !x.locked; });
    var rows = order.map(function (key, i) {
      var col = d.columns.filter(function (x) { return x.key === key; })[0] || { key: key, label: key };
      var locked = !!col.locked;
      var opts = locked ? '<option>' + esc(col.label) + "</option>" : swappable.map(function (x) {
        return '<option value="' + esc(x.key) + '"' + (x.key === key ? " selected" : "") + ">" + esc(x.label) + "</option>";
      }).join("");
      return '<li class="fld' + (locked ? " locked" : "") + '" data-fld="' + esc(key) + '"' + (locked ? "" : ' draggable="true"') + ">" +
        '<button type="button" class="fld-grip" data-fld-grip aria-label="Move ' + esc(col.label) + '"' + (locked ? ' disabled aria-disabled="true" title="This column stays in place"' : ' title="Drag, or use the arrow keys"') + ">" + svg("grip-vertical", "sm") + "</button>" +
        '<span class="fld-sel">' + svg(icons[key] || "list", "sm") + '<select data-fld-swap aria-label="Field in position ' + (i + 1) + '"' + (locked ? " disabled" : "") + ">" + opts + "</select>" + svg(locked ? "lock" : "chevron-down", "sm fld-caret") + "</span>" +
        '<button type="button" class="fld-x" data-fld-remove aria-label="Remove ' + esc(col.label) + '"' + (locked ? ' disabled aria-disabled="true" title="Always shown"' : ' title="Remove from the table"') + ">" + svg("x", "sm") + "</button></li>";
    });
    rows.push('<li class="fld locked"><span class="fld-grip">' + svg("grip-vertical", "sm") + '</span><span class="fld-sel">' + svg("ellipsis", "sm") +
      '<span class="fld-fixed">Actions</span>' + svg("lock", "sm fld-caret") + '</span><span class="fld-x" aria-hidden="true">' + svg("x", "sm") + "</span></li>");
    list.innerHTML = rows.join("");
    var avail = $("[data-fld-avail]", c), hidden = swappable.filter(function (x) { return order.indexOf(x.key) < 0; });
    if (avail) {
      avail.innerHTML = hidden.length ? hidden.map(function (x) {
        return '<li data-col-row data-text="' + esc(x.label.toLowerCase()) + '"><button type="button" class="fld-avail-btn" data-fld-addkey="' + esc(x.key) + '">' +
          svg(icons[x.key] || "list", "sm") + "<span>" + esc(x.label) + "</span>" + svg("plus", "sm") + "</button></li>";
      }).join("") : '<li class="muted fld-none">Every field is already in the table.</li>';
    }
    $$("[data-fld-hidden]", c).forEach(function (el) { el.textContent = String(hidden.length); });
  }
  function moveField(key, delta) {
    var order = colOrder(), i = order.indexOf(key), j = i + delta;
    var d = fieldData(), lockedN = d.columns.filter(function (x) { return x.locked; }).length;
    if (i < 0 || j < lockedN || j >= order.length) return;
    order.splice(i, 1); order.splice(j, 0, key);
    saveOrder(order, labelOf(key) + " moved to position " + (j + 1));
    var grip = $('[data-fld="' + key + '"] [data-fld-grip]', chooser()); if (grip) grip.focus();
  }
  /* ---- the drawer ---- */
  var ssView = null; /* open view, kept across workspace swaps */
  function showView(name) {
    var c = chooser(); if (!c) return;
    $$("[data-ss-view]", c).forEach(function (v) { v.hidden = v.getAttribute("data-ss-view") !== name; });
    ssView = name;
    if (name === "fields") renderFields(colOrder() || []);
    var head = $('[data-ss-view="' + name + '"] h2', c); if (head) { head.tabIndex = -1; head.focus({ preventScroll: true }); }
  }
  function openPanel(id, view) {
    var p = doc.getElementById(id); if (!p) return;
    p.hidden = false;
    doc.documentElement.classList.add("ss-open");
    if (p.id === "col-chooser") showView(view || "fields");
  }
  function closePanels() {
    $$(".sidepanel").forEach(function (p) { p.hidden = true; });
    doc.documentElement.classList.remove("ss-open");
    ssView = null;
  }
  var dragKey = null;
  doc.addEventListener("dragstart", function (e) {
    var li = e.target.closest && e.target.closest(".fld[draggable]"); if (!li) return;
    dragKey = li.getAttribute("data-fld"); li.classList.add("dragging");
    try { e.dataTransfer.setData("text/plain", dragKey); e.dataTransfer.effectAllowed = "move"; } catch (err) { /* old browsers */ }
  });
  doc.addEventListener("dragover", function (e) {
    var li = dragKey && e.target.closest && e.target.closest(".fld[data-fld]"); if (!li) return;
    e.preventDefault();
    $$(".fld.drop-before, .fld.drop-after").forEach(function (x) { x.classList.remove("drop-before", "drop-after"); });
    var r = li.getBoundingClientRect();
    li.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after");
  });
  doc.addEventListener("drop", function (e) {
    var li = dragKey && e.target.closest && e.target.closest(".fld[data-fld]"); if (!li) return;
    e.preventDefault();
    var target = li.getAttribute("data-fld"), after = li.classList.contains("drop-after");
    var order = colOrder(), d = fieldData(), lockedN = d.columns.filter(function (x) { return x.locked; }).length;
    order.splice(order.indexOf(dragKey), 1);
    var at = order.indexOf(target) + (after ? 1 : 0);
    order.splice(Math.max(lockedN, at), 0, dragKey);
    var moved = dragKey; dragKey = null;
    saveOrder(order, labelOf(moved) + " moved to position " + (order.indexOf(moved) + 1));
  });
  doc.addEventListener("dragend", function () {
    dragKey = null;
    $$(".fld.dragging, .fld.drop-before, .fld.drop-after").forEach(function (x) { x.classList.remove("dragging", "drop-before", "drop-after"); });
  });
  doc.addEventListener("keydown", function (e) {
    var g = e.target.closest && e.target.closest("[data-fld-grip]");
    if (!g || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    moveField(g.closest("[data-fld]").getAttribute("data-fld"), e.key === "ArrowUp" ? -1 : 1);
  });

  /* ---------- tag inputs (titles, keywords) ---------- */
  function addTag(input, raw) {
    var box = input.closest("[data-tagbox]"); if (!box) return;
    var name = input.name.replace(/_add$/, "");
    input.value = ""; /* clear first: the change event below sends the form */
    String(raw).split(",").forEach(function (part) {
      var v = part.trim().replace(/^"|"$/g, ""); if (!v) return;
      var dup = $$("input[type=hidden]", box).some(function (h) { return h.value.toLowerCase() === v.toLowerCase(); });
      if (dup) return;
      var tag = doc.createElement("span"); tag.className = "tag";
      var hid = doc.createElement("input"); hid.type = "hidden"; hid.name = name; hid.value = v;
      var tt = doc.createElement("span"); tt.className = "tt"; tt.textContent = v;
      var x = doc.createElement("a"); x.className = "tx"; x.href = "#"; x.setAttribute("data-tag-remove", ""); x.setAttribute("aria-label", "Remove " + v); x.textContent = "×";
      tag.appendChild(hid); tag.appendChild(tt); tag.appendChild(x);
      input.parentNode.insertBefore(tag, input);
      fire(hid, "change");
    });
  }
  doc.addEventListener("keydown", function (e) {
    var t = e.target;
    if (!t.classList || !t.classList.contains("tag-input")) return;
    if (e.key === "Enter" || e.key === ",") {
      if (t.value.trim()) { e.preventDefault(); addTag(t, t.value); }
      else if (e.key === ",") e.preventDefault();
    } else if (e.key === "Backspace" && !t.value) {
      var tags = $$(".tag", t.parentNode), last = tags[tags.length - 1];
      if (last) { last.remove(); fire(t, "change"); }
    }
  });

  /* panel groups stay open while filters re-render the workspace */
  var openGroups = {};
  doc.addEventListener("toggle", function (e) {
    var g = e.target;
    if (g.matches && g.matches(".fpanel .fgroup[data-fgroup]")) openGroups[g.getAttribute("data-fgroup")] = g.open;
  }, true);
  function restoreOpenGroups() {
    $$(".fpanel .fgroup[data-fgroup]").forEach(function (g) {
      var key = g.getAttribute("data-fgroup");
      if (key in openGroups && g.open !== openGroups[key]) g.open = openGroups[key];
    });
  }

  /* ---------- filter pins (side panel shows pinned groups; All filters edits the pins) ---------- */
  function pinKey() { return "lc-pins-" + kind(); }
  function pins() {
    var panel = $(".fpanel"); if (!panel) return [];
    var saved = lsJson(pinKey());
    if (Array.isArray(saved)) return saved;
    return (panel.getAttribute("data-pin-defaults") || "").split(",").filter(Boolean);
  }
  function applyPins() {
    var list = pins();
    $$(".fpanel .fgroup[data-fgroup]").forEach(function (g) {
      var on = list.indexOf(g.getAttribute("data-fgroup")) >= 0 || Number(g.getAttribute("data-count")) > 0;
      g.classList.toggle("unpinned", !on);
    });
    var box = $('[data-flist="pinned"]');
    if (!box) return;
    var groups = $$(".allf .fgroup[data-fgroup]");
    groups.forEach(function (g, i) { if (!g.hasAttribute("data-order")) g.setAttribute("data-order", String(i)); });
    groups.sort(function (a, b) { return Number(a.getAttribute("data-order")) - Number(b.getAttribute("data-order")); });
    groups.forEach(function (g) {
      var key = g.getAttribute("data-fgroup"), pinned = list.indexOf(key) >= 0;
      var btn = $("[data-pin]", g); if (btn) btn.setAttribute("aria-pressed", String(pinned));
      if (pinned) { box.appendChild(g); return; }
      var origin = $('[data-flist="' + g.getAttribute("data-fcol") + '"]');
      if (origin) origin.insertBefore(g, $(".fgroup.locked", origin));
    });
    var empty = $(".pins-empty", box);
    if (empty) empty.hidden = $$(".fgroup[data-fgroup]", box).length > 0;
  }
  function togglePin(key) {
    var list = pins().slice(), i = list.indexOf(key);
    if (i >= 0) list.splice(i, 1); else list.push(key);
    lsSet(pinKey(), JSON.stringify(list));
    applyPins();
  }
  /* live "N records found": plain fetch, because app.js closes any dialog that issues an htmx call */
  var countTimer = null, countSeq = 0;
  function refreshCount(delay) {
    clearTimeout(countTimer);
    countTimer = setTimeout(function () {
      var out = $("#allf-count"), form = $("#allf");
      if (!out || !form || !window.fetch) return;
      var params = new URLSearchParams(new FormData(form));
      ["tab", "sort", "per"].forEach(function (k) { params.delete(k); });
      var base = out.getAttribute("data-count-url"), seq = ++countSeq;
      fetch(base + "&" + params.toString(), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
        .then(function (html) { if (seq === countSeq) out.innerHTML = html; })
        .catch(function () { if (seq === countSeq) out.textContent = "Count unavailable"; });
    }, delay === undefined ? 350 : delay);
  }
  function filterModalRows(q) {
    q = q.trim().toLowerCase();
    $$(".allf .fgroup").forEach(function (g) {
      g.hidden = !!q && (g.getAttribute("data-label") || "").indexOf(q) < 0;
    });
  }
  function setType(v) {
    var d = $("#allf-dialog"); if (!d) return;
    $$("[data-fbox]", d).forEach(function (col) {
      var k = col.getAttribute("data-fbox");
      col.hidden = (k === "person" && v === "company") || (k === "company" && v === "person");
    });
  }

  /* ---------- lists: searchable checklists, view menu, save-to-list window ---------- */
  doc.addEventListener("input", function (e) {
    var t = e.target; if (!t.matches) return;
    if (t.closest("#allf") && !t.matches("[data-list-search], [data-allf-search], [data-type-select]")) refreshCount();
    if (t.matches("[data-list-search]")) {
      var scope = t.closest(".checklist") || t.closest(".sd-body");
      var q = t.value.trim().toLowerCase();
      $$(".opt[data-text]", scope).forEach(function (o) { o.hidden = !!q && o.getAttribute("data-text").indexOf(q) < 0; });
    } else if (t.matches("[data-view-search]")) {
      filterViews();
    } else if (t.matches("[name=dest_new]") && t.closest("#save-dialog")) {
      refreshSummary(500);
    } else if (t.matches("[data-col-search]")) {
      var cq = t.value.trim().toLowerCase();
      $$("[data-col-row]").forEach(function (r) { r.hidden = !!cq && r.getAttribute("data-text").indexOf(cq) < 0; });
    } else if (t.matches("[data-allf-search]")) {
      filterModalRows(t.value);
    } else if (t.id === "sd-new") {
      var nr = $("[data-new-radio]"); if (nr && t.value) nr.checked = true;
    }
  });

  function selectedCount() { return $$("input.rowcb:checked").length; }
  function updateSelCount() {
    $$("[data-sel-count]").forEach(function (el) { el.textContent = String(selectedCount()); });
  }
  function clearSelection() {
    $$("input.rowcb, input.allcb").forEach(function (b) { b.checked = false; b.indeterminate = false; });
    var first = $("input.rowcb"); if (first) fire(first, "change"); else updateSelCount();
  }
  /* ---------- the save window ---------- */
  var sumTimer = null, sumSeq = 0;
  function refreshSummary(delay) {
    clearTimeout(sumTimer);
    sumTimer = setTimeout(function () {
      var form = $("#bulk"), out = $("[data-save-summary]");
      if (!form || !out || !window.fetch) return;
      var seq = ++sumSeq;
      fetch("/partials/save-summary", { method: "POST", body: new FormData(form), credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
        .then(function (html) { if (seq === sumSeq) out.innerHTML = html; })
        .catch(function () { if (seq === sumSeq) out.innerHTML = '<p class="muted">The summary is not available right now. Save still works.</p>'; });
    }, delay === undefined ? 120 : delay);
  }
  function syncSaveForm() {
    var d = $("#save-dialog"); if (!d) return;
    var dest = $("[name=dest_list]", d), nm = $("[data-save-new]", d);
    if (dest && nm) { nm.hidden = dest.value !== "new"; if (!nm.hidden && !nm.value) nm.focus(); }
    var find = $("[name=find]", d), seg = $(".seg2", d);
    if (find && seg && !find.disabled) {
      seg.classList.toggle("dim", !find.checked);
      $$("input", seg).forEach(function (r) { r.disabled = !find.checked; });
    }
  }
  function openSave() {
    var d = $("#save-dialog"); if (!d) return;
    updateSelCount(); syncSaveForm();
    var out = $("[data-save-summary]", d); if (out) out.innerHTML = '<p class="muted">Working out what Save will do…</p>';
    if (d.showModal && !d.open) d.showModal();
    refreshSummary(0);
  }
  function exportKept(form) {
    var f = doc.createElement("form");
    f.method = "post"; f.action = "/bulk/export.csv"; f.hidden = true;
    var fd = new FormData(form);
    ["kind", "ids", "keep", "keep_sent"].forEach(function (k) {
      fd.getAll(k).forEach(function (v) { var i = doc.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i); });
    });
    var a = doc.createElement("input"); a.type = "hidden"; a.name = "apply_keep"; a.value = "1"; f.appendChild(a);
    doc.body.appendChild(f); f.submit(); setTimeout(function () { f.remove(); }, 1000);
  }

  /* ---------- saved-view menu: search, tabs, row actions ---------- */
  var vtab = "all";
  function filterViews() {
    var menu = $(".viewmenu"); if (!menu) return;
    var box = $("[data-view-search]", menu), q = box ? box.value.trim().toLowerCase() : "", n = 0;
    $$("[data-vrow]", menu).forEach(function (r) {
      var ok = (!q || r.getAttribute("data-text").indexOf(q) >= 0) &&
        (vtab === "all" || (vtab === "yours" && r.getAttribute("data-system") === "0") ||
         (vtab === "starred" && r.getAttribute("data-starred") === "1"));
      r.hidden = !ok; if (ok) n++;
    });
    var empty = $("[data-vempty]", menu);
    if (empty) {
      empty.hidden = n > 0;
      empty.textContent = q ? "No saved search matches." : vtab === "starred"
        ? "Nothing starred yet. Open the menu of one of your searches and choose Star."
        : "You have not saved a search yet. Apply some filters, then use Create saved search.";
    }
    $$("[data-vtab]", menu).forEach(function (b) { b.setAttribute("aria-selected", String(b.getAttribute("data-vtab") === vtab)); });
  }
  function reloadWorkspace(after) {
    if (!window.htmx) { location.reload(); return; }
    window.htmx.ajax("GET", location.pathname + location.search, { target: "#workspace", select: "#workspace", swap: "outerHTML" })
      .then(function () { if (after) setTimeout(after, 30); });
  }
  function reopenViewMenu() { var m = $("details[data-view-menu]"); if (m) { m.open = true; filterViews(); } }
  function postForm(form, action) {
    return fetch(action || form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin", headers: { "HX-Request": "true" } })
      .then(function (r) {
        var trig = r.headers.get("HX-Trigger") || "", ok = r.ok && trig.indexOf('"error"') < 0;
        try { var msg = JSON.parse(trig).toast; if (msg) doc.body.dispatchEvent(new CustomEvent("toast", { detail: msg, bubbles: true })); } catch (err) { /* no toast */ }
        return ok;
      });
  }
  doc.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.matches || !f.matches("[data-view-rename-form]") || !window.fetch) return;
    e.preventDefault();
    var dlg = $("dialog", f);
    postForm(f).then(function (ok) { if (ok) { if (dlg) dlg.close(); reloadWorkspace(reopenViewMenu); } });
  });

  /* ---------- tabs (company panel, person page) ---------- */
  function lazy() { if (window.lcInitLazy) window.lcInitLazy(); }
  function selectTab(bar, name) {
    var scope = bar.parentElement;
    $$("[data-tab]", bar).forEach(function (a) { a.setAttribute("aria-selected", String(a.getAttribute("data-tab") === name)); });
    $$("[data-panel]", scope).forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== name; });
    lazy();
  }
  function initTabs() {
    $$("[data-tabs]").forEach(function (bar) {
      var first = $("[data-tab]", bar), hash = location.hash.replace("#", "");
      var match = hash && $$("[data-tab]", bar).filter(function (a) { return a.getAttribute("href") === "#" + hash; })[0];
      var current = $('[data-tab][aria-selected="true"]', bar);
      selectTab(bar, (match || current || first).getAttribute("data-tab"));
    });
  }

  /* ---------- clicks ---------- */
  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : e.target.parentElement;
    if (!t || !t.closest) return;
    var el;
    if ((el = t.closest("[data-pin]"))) { e.preventDefault(); e.stopPropagation(); togglePin(el.getAttribute("data-pin")); return; }
    if ((el = t.closest("[data-tag-remove]"))) {
      e.preventDefault();
      var tag = el.closest(".tag"), input = $(".tag-input", el.closest("[data-tagbox]"));
      if (tag) tag.remove();
      if (input) fire(input, "change");
      return;
    }
    if ((el = t.closest("[data-open-panel]"))) {
      var dd = el.closest("details.dd"); if (dd) dd.open = false;
      openPanel(el.getAttribute("data-open-panel"), el.getAttribute("data-panel-view")); return;
    }
    if (t.closest("[data-close-panel]")) { closePanels(); return; }
    if ((el = t.closest("[data-ss-go]"))) { showView(el.getAttribute("data-ss-go")); return; }
    if ((el = t.closest("[data-vtab]"))) { vtab = el.getAttribute("data-vtab"); filterViews(); return; }
    if ((el = t.closest("[data-view-rename]"))) {
      var rf = $("[data-view-rename-form]"), rd = $("#rename-dialog");
      if (!rf || !rd) return;
      rf.setAttribute("action", "/searches/" + encodeURIComponent(el.getAttribute("data-view-rename")) + "/rename");
      var rn = $("#rn-name"); rn.value = el.getAttribute("data-view-name") || "";
      $$("details.dd[open]").forEach(function (d) { d.open = false; });
      rd.showModal(); setTimeout(function () { rn.focus(); rn.select(); }, 30);
      return;
    }
    if ((el = t.closest("[data-fld-remove]"))) {
      if (el.disabled) return;
      var rk = el.closest("[data-fld]").getAttribute("data-fld"), ro = colOrder();
      ro.splice(ro.indexOf(rk), 1); saveOrder(ro, labelOf(rk) + " removed from the table"); return;
    }
    if ((el = t.closest("[data-fld-addkey]"))) {
      var ak = el.getAttribute("data-fld-addkey"), ao = colOrder();
      if (ao.indexOf(ak) < 0) ao.push(ak);
      saveOrder(ao, labelOf(ak) + " added at the end"); return;
    }
    if ((el = t.closest("[data-fld-add]"))) {
      var pick = $("[data-fld-pick]", chooser()), open = pick.hidden;
      pick.hidden = !open; el.setAttribute("aria-expanded", String(open));
      if (open) { var sf = $("input[type=search]", pick); if (sf) sf.focus(); }
      return;
    }
    if ((el = t.closest("[data-open-dialog]"))) {
      var menu = el.closest("details.dd"); if (menu) menu.open = false;
      var id = el.getAttribute("data-open-dialog");
      if (id === "save-dialog" || id === "task-dialog" || id === "list-dialog") updateSelCount();
      if (id === "save-dialog") {
        syncSaveForm();
        var so = $("[data-save-summary]"); if (so) so.innerHTML = '<p class="muted">Working out what Save will do…</p>';
        refreshSummary(0);
      }
      if (id === "allf-dialog") { applyPins(); refreshCount(0); }
      if (id === "savesearch-dialog") { var nm = $("#ss-name"); if (nm) setTimeout(function () { nm.focus(); }, 30); }
    }
    if (t.closest("[data-col-reset]")) {
      var c = chooser(); if (c) { lsDel(c.getAttribute("data-key")); applyCols(); }
      var lv = c && $("[data-fld-live]", c); if (lv) lv.textContent = "Columns reset to the default set";
      return;
    }
    if ((el = t.closest("[data-clear-selection]"))) {
      var d1 = el.closest("details.dd"); if (d1) d1.open = false;
      clearSelection(); return;
    }
    if ((el = t.closest("[data-select-all]"))) {
      var d2 = el.closest("details.dd"); if (d2) d2.open = false;
      $$("input.rowcb").forEach(function (b) { b.checked = true; });
      var one = $("input.rowcb"); if (one) fire(one, "change");
      return;
    }
    if ((el = t.closest("[data-row-save]"))) {
      var row = el.closest("tr"), box = row && $("input.rowcb", row);
      if (box) {
        $$("input.rowcb").forEach(function (b) { b.checked = false; });
        box.checked = true; fire(box, "change"); openSave();
      }
      return;
    }
    /* data-copy-value (not data-copy: tools.js owns that one for Assistant messages) */
    if ((el = t.closest("[data-copy-value]"))) {
      e.stopPropagation();
      var text = el.getAttribute("data-copy-value");
      var done = function () { toast("Copied " + text); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, done);
      else {
        var ta = doc.createElement("textarea"); ta.value = text; doc.body.appendChild(ta); ta.select();
        try { doc.execCommand("copy"); } catch (err) { /* ignore */ }
        ta.remove(); done();
      }
      return;
    }
    if ((el = t.closest("[data-tab]"))) {
      var href = el.getAttribute("href") || "";
      if (href.charAt(0) === "#") { e.preventDefault(); selectTab(el.closest("[data-tabs]"), el.getAttribute("data-tab")); }
      return;
    }
    if (t.closest("[data-toggle-filters]")) { syncFiltersLabel(); }
    var dlg = t.closest && t.tagName === "DIALOG" ? t : null;
    if (dlg && dlg.id !== "palette" && e.target === dlg) { dlg.close(); return; }
    var panel = chooser();
    if (panel && !panel.hidden && !t.closest("#col-chooser") && !t.closest("[data-open-panel]") && !t.closest("dialog") && !t.closest(".toast")) closePanels();
  });

  doc.addEventListener("change", function (e) {
    var t = e.target; if (!t.matches) return;
    if (t.closest("#allf") && !t.matches("[data-list-search], [data-allf-search], [data-type-select]")) refreshCount();
    if (t.matches("[data-fld-swap]")) {
      var from = t.closest("[data-fld]").getAttribute("data-fld"), to = t.value, so = colOrder();
      var i = so.indexOf(from), j = so.indexOf(to);
      if (j >= 0) { so[j] = from; } /* already shown elsewhere: the two swap places */
      so[i] = to;
      saveOrder(so, labelOf(to) + " now in position " + (i + 1));
    } else if (t.matches("[data-auto-switch]")) {
      var af = t.closest("form"), fd = new FormData(af);
      if (!t.checked) fd.set("auto", "0");
      fetch(af.getAttribute("action"), { method: "POST", body: fd, credentials: "same-origin", headers: { "HX-Request": "true" } })
        .then(function (r) {
          var trig = r.headers.get("HX-Trigger");
          try { var msg = JSON.parse(trig).toast; doc.body.dispatchEvent(new CustomEvent("toast", { detail: msg, bubbles: true })); } catch (err) { /* no toast */ }
          if (trig && trig.indexOf('"error"') >= 0) t.checked = !t.checked;
        })
        .catch(function () { t.checked = !t.checked; toast("Could not save the setting."); });
    } else if (t.matches("[data-page-select]")) {
      var url = t.value;
      if (window.htmx) {
        window.htmx.ajax("GET", url, { target: "#workspace", select: "#workspace", swap: "outerHTML" })
          .then(function () { history.pushState({}, "", url); });
      } else location.href = url;
    } else if (t.matches("[data-type-select]")) setType(t.value);
    else if (t.matches('input[name="list_id"]') && t.getAttribute("data-new-radio") === null) {
      var nn = $("#sd-new"); if (nn) nn.value = "";
    } else if (t.classList.contains("rowcb") || t.classList.contains("allcb")) updateSelCount();
    if (t.closest("#save-dialog")) {
      syncSaveForm();
      if (!t.matches("[name=dest_new]")) refreshSummary();
    }
  });

  /* a min-max pair is sent once, when focus has left both fields (a swap would eat the second) */
  var rangeTimer = null;
  doc.addEventListener("change", function (e) {
    var t = e.target;
    if (e.lcPass || !t.matches || !t.matches("[name=size_min], [name=size_max]") || !t.closest("#f")) return;
    e.stopImmediatePropagation();
    clearTimeout(rangeTimer);
    rangeTimer = setTimeout(function () {
      var a = doc.activeElement;
      if (a && a.matches && a.matches("[name=size_min], [name=size_max]")) return;
      var ev = new Event("change", { bubbles: true }); ev.lcPass = true; t.dispatchEvent(ev);
    }, 450);
  }, true);

  doc.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    closePanels();
    $$("details.dd[open]").forEach(function (d) { d.open = false; });
  });

  /* ---------- request hygiene ---------- */
  /* keep URLs clean: no empty values, no defaults, "similar titles" only when switched off */
  doc.body.addEventListener("htmx:configRequest", function (e) {
    var d = e.detail, el = d.elt;
    if (String(d.verb).toLowerCase() !== "get" || !el || (el.id !== "f" && el.id !== "allf")) return;
    var fd = d.formData; if (!fd) return;
    var seen = {};
    Array.from(fd.keys()).forEach(function (k) {
      if (seen[k]) return;
      seen[k] = true;
      var vals = fd.getAll(k).filter(function (x) { return x !== ""; });
      if (k === "similar") vals = vals.indexOf("1") >= 0 ? [] : vals.slice(0, 1);
      else if ((k === "per" && vals[0] === "25") || (k === "sort" && vals[0] === "relevance") ||
               (k === "tab" && vals[0] === "all")) vals = [];
      fd.delete(k);
      vals.forEach(function (v) { fd.append(k, v); });
    });
  });
  doc.addEventListener("htmx:afterRequest", function (e) {
    var el = e.detail && e.detail.elt; if (!el || !el.closest || !e.detail.successful) return;
    var pop = el.closest(".addlist"); if (pop) { var dd = pop.closest("details.dd"); if (dd) dd.open = false; }
    var hdr = (e.detail.xhr && e.detail.xhr.getResponseHeader("HX-Trigger")) || "";
    var win = el.closest("#save-dialog, #list-dialog");
    if (win) {
      if (hdr.indexOf('"error"') >= 0) { /* nothing was saved: keep the window open */
        if (!win.open) win.showModal();
        return;
      }
      if (hdr.indexOf('"lc-export"') >= 0) exportKept($("#bulk"));
      clearSelection();
      /* re-render the results so "Saved" chips, counts and the list sizes are current */
      reloadWorkspace();
      return;
    }
    if (el.closest("[data-view-act]") && hdr.indexOf('"error"') < 0) reloadWorkspace(reopenViewMenu);
  });

  function syncFiltersLabel() {
    setTimeout(function () {
      var ws = $(".ws");
      root.classList.toggle("filters-off", !!(ws && ws.classList.contains("filters-hidden")));
    }, 0);
  }

  function init() {
    applyPrefs(); applyCols(); applyPins(); restoreOpenGroups(); initTabs(); updateSelCount(); syncFiltersLabel(); lazy();
  }
  doc.addEventListener("htmx:afterSettle", init);
  restorePerPage();
  init();
})();

/* AI search: plain words -> filters (POST /api/ai/parse), then open the filtered page. */
(function () {
  async function run(box) {
    var input = box.querySelector("[data-ai-input]");
    var out = box.querySelector("[data-ai-out]");
    var text = (input.value || "").trim();
    if (!text) { input.focus(); return; }
    out.textContent = "Reading your request…";
    try {
      var r = await fetch("/api/ai/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, kind: box.dataset.kind })
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      var d = await r.json();
      if (!d.query) {
        out.textContent = "Nothing to turn into filters. Try a country, a size, an industry or a job title.";
        return;
      }
      out.textContent = (d.explanation || []).join(" · ");
      window.location.assign(d.url);
    } catch (e) {
      out.textContent = "AI search is unavailable right now. Use the filters on the left.";
    }
  }
  document.addEventListener("click", function (e) {
    var go = e.target.closest("[data-ai-go]");
    if (go) run(go.closest("[data-ai-box]"));
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.matches && e.target.matches("[data-ai-input]")) {
      e.preventDefault();
      run(e.target.closest("[data-ai-box]"));
    }
  });
})();

/* Profile + row actions: dialogs from other features, lazy panels, insights tabs, AI column. */
(function () {
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function toast(message, kind) {
    doc.body.dispatchEvent(new CustomEvent("toast", { detail: { message: message, kind: kind }, bubbles: true }));
  }
  function selected() { return $$("input.rowcb:checked").map(function (b) { return b.value; }); }
  function closeMenus() { $$("details.dd[open]").forEach(function (d) { d.open = false; }); }
  function process(el) { if (window.htmx) window.htmx.process(el); }
  function get(url) {
    return fetch(url, { credentials: "same-origin", headers: { "HX-Request": "true" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); });
  }

  /* ---- dialogs provided by other features (email, sequence, task, deal, note, call, meeting) ---- */
  function host() {
    var h = $("#lc-dlg-host");
    if (!h) { h = doc.createElement("div"); h.id = "lc-dlg-host"; doc.body.appendChild(h); }
    return h;
  }
  function openDialog(url) {
    get(url).then(function (html) {
      var h = host();
      h.innerHTML = html;
      var dlg = $("dialog", h);
      if (!dlg) {
        h.innerHTML = '<dialog class="sd host-fragment"><header class="allf-head"><h2>Details</h2>' +
          '<button type="button" class="btn ghost icon-only close-dlg" aria-label="Close">×</button></header>' +
          '<div class="sd-body"></div></dialog>';
        dlg = $("dialog", h); $(".sd-body", dlg).innerHTML = html;
      }
      process(h);
      dlg.addEventListener("close", function () { h.innerHTML = ""; });
      if (dlg.showModal) dlg.showModal();
    }).catch(function (status) {
      toast(status === 404 ? "This action is not available in this build yet." : "Could not open it (" + status + ").", "error");
    });
  }

  /* ---- lazy panels: activity + company insights ---- */
  function loadInto(el, url, failure) {
    if (el.getAttribute("data-loaded") === url) return;
    el.setAttribute("data-loaded", url);
    get(url).then(function (html) { el.innerHTML = html; process(el); })
      .catch(function () { el.removeAttribute("data-loaded"); el.innerHTML = '<p class="muted">' + failure + "</p>"; });
  }
  window.lcInitLazy = initLazy;
  function initLazy() {
    $$("[data-lazy-src]").forEach(function (el) {
      if (!el.closest("[hidden]")) {
        loadInto(el, el.getAttribute("data-lazy-src"), el.getAttribute("data-lazy-fail") || "The activity timeline is not available in this build yet.");
      }
    });
  }
  doc.addEventListener("htmx:afterSettle", initLazy);

  /* ---- AI prompt on the selected rows ---- */
  var aiMode = "prompt";
  function openAi(mode, text) {
    var ids = selected();
    if (!ids.length) { toast("Select some rows first, then run a prompt on them."); return; }
    var dlg = $("#ai-dialog"); if (!dlg) return;
    aiMode = mode;
    $("[data-ai-title]", dlg).textContent = mode === "formula" ? "Generate a formula column" : "Run a custom prompt";
    $("[data-ai-label]", dlg).textContent = mode === "formula" ? "Describe the value to compute" : "Your prompt";
    $("[data-ai-prompt]", dlg).placeholder = mode === "formula" ? "e.g. Number of employees at the top of the range" : $("[data-ai-prompt]", dlg).placeholder;
    $("[data-ai-prompt]", dlg).value = text || "";
    $("[data-ai-status]", dlg).textContent = ids.length > 25 ? "Only the first 25 selected rows are sent." : "";
    $$("[data-sel-count]", dlg).forEach(function (el) { el.textContent = String(ids.length); });
    dlg.showModal();
    $("[data-ai-prompt]", dlg).focus();
  }
  function removeTemp() { $$("[data-temp]").forEach(function (el) { el.remove(); }); }
  function addTempColumn(title, answers) {
    removeTemp();
    var head = $("table.data thead tr"); if (!head) return;
    var th = doc.createElement("th");
    th.setAttribute("data-temp", ""); th.scope = "col"; th.title = title;
    th.textContent = "AI: " + (title.length > 24 ? title.slice(0, 24) + "…" : title);
    var x = doc.createElement("button");
    x.type = "button"; x.className = "btn ghost icon-only sm temp-x"; x.setAttribute("data-temp-remove", "");
    x.setAttribute("aria-label", "Remove this column"); x.title = "Remove this column"; x.textContent = "×";
    th.appendChild(x);
    head.insertBefore(th, $("th.addcol", head));
    $$("table.data tbody tr").forEach(function (tr) {
      var td = doc.createElement("td"); td.setAttribute("data-temp", "");
      var id = tr.getAttribute("data-id");
      if (id && answers[id] !== undefined) td.textContent = answers[id];
      else { td.textContent = "Not run"; td.className = "na"; }
      tr.insertBefore(td, $("td.ract", tr));
    });
  }
  function runAi() {
    var dlg = $("#ai-dialog"), status = $("[data-ai-status]", dlg);
    var prompt = $("[data-ai-prompt]", dlg).value.trim(), ids = selected().slice(0, 25);
    if (!prompt) { status.textContent = "Write a prompt first."; return; }
    var btn = $("[data-ai-run]", dlg); btn.disabled = true;
    status.textContent = "Asking your model about " + ids.length + " row" + (ids.length === 1 ? "" : "s") + "…";
    var fd = new FormData();
    fd.append("kind", dlg.getAttribute("data-kind")); fd.append("prompt", prompt); fd.append("mode", aiMode);
    ids.forEach(function (id) { fd.append("ids", id); });
    fetch("/bulk/ai-prompt", { method: "POST", body: fd, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) { status.textContent = res.d.error || "The model did not answer."; return; }
        addTempColumn(res.d.title || prompt, res.d.answers || {});
        dlg.close(); toast("Added a temporary column. It disappears when you change the search.");
      })
      .catch(function () { status.textContent = "Could not reach the server."; })
      .then(function () { btn.disabled = false; });
  }

  /* ---- row menus float above the table (it scrolls and would clip them) ---- */
  doc.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.matches || !d.matches(".ract-dd") || !d.open) return;
    var menu = $(".ract-menu", d), r = $("summary", d).getBoundingClientRect();
    menu.style.position = "fixed"; menu.style.left = "auto"; menu.style.right = Math.max(8, window.innerWidth - r.right) + "px";
    var h = menu.offsetHeight;
    menu.style.top = (r.bottom + 4 + h > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
  }, true);
  doc.addEventListener("scroll", function (e) {
    if (e.target && e.target.nodeType === 1 && e.target.closest && e.target.closest(".ract-dd")) return;
    $$(".ract-dd[open]").forEach(function (d) { d.open = false; });
  }, true);

  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : e.target.parentElement;
    if (!t || !t.closest) return;
    var el;
    if ((el = t.closest("[data-dlg]"))) {
      e.preventDefault(); closeMenus(); openDialog(el.getAttribute("href")); return;
    }
    if ((el = t.closest("[data-dlg-ids]"))) {
      var ids = selected(); closeMenus();
      if (!ids.length) { toast("Select at least one row first."); return; }
      openDialog(el.getAttribute("data-dlg-ids").replace("{ids}", encodeURIComponent(ids.join(",")))); return;
    }
    if ((el = t.closest("[data-dlg-one]"))) {
      var one = selected(); closeMenus();
      if (one.length !== 1) { toast(el.getAttribute("data-need-msg") || "Select exactly one row."); return; }
      openDialog(el.getAttribute("data-dlg-one").replace("{id}", encodeURIComponent(one[0]))); return;
    }
    if ((el = t.closest("[data-ai-open]"))) {
      if (el.disabled) return;
      closeMenus(); openAi(el.getAttribute("data-ai-open"), el.getAttribute("data-ai-text")); return;
    }
    if (t.closest("[data-ai-run]")) { runAi(); return; }
    if (t.closest("[data-temp-remove]")) { removeTemp(); return; }
  });
})();
