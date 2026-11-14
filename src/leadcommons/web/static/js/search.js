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

  /* ---------- columns ---------- */
  function chooser() { return $("#col-chooser"); }
  function visibleCols() {
    var c = chooser(); if (!c) return null;
    var saved = lsJson(c.getAttribute("data-key"));
    var out = {};
    $$("[data-col-toggle]", c).forEach(function (box) {
      var key = box.getAttribute("data-col-toggle");
      var def = box.getAttribute("data-default") === "1";
      out[key] = box.disabled ? true : (Array.isArray(saved) ? saved.indexOf(key) >= 0 : def);
    });
    return out;
  }
  function applyCols() {
    var c = chooser(); if (!c) return;
    var vis = visibleCols(), n = 0;
    $$("[data-col-toggle]", c).forEach(function (box) {
      var on = vis[box.getAttribute("data-col-toggle")];
      box.checked = on; if (on) n++;
    });
    $$("table[data-cols] [data-col]").forEach(function (cell) { cell.hidden = !vis[cell.getAttribute("data-col")]; });
    $$("[data-col-count]").forEach(function (el) { el.textContent = String(n); });
  }
  function saveCols() {
    var c = chooser(); if (!c) return;
    var keys = $$("[data-col-toggle]", c).filter(function (b) { return b.checked; })
      .map(function (b) { return b.getAttribute("data-col-toggle"); });
    lsSet(c.getAttribute("data-key"), JSON.stringify(keys));
    applyCols();
  }
  function openPanel(id) {
    var p = doc.getElementById(id); if (!p) return;
    p.hidden = false;
    var f = $("input[type=search]", p); if (f) f.focus();
  }
  function closePanels() { $$(".sidepanel").forEach(function (p) { p.hidden = true; }); }

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
      var vq = t.value.trim().toLowerCase();
      $$(".vitem").forEach(function (v) { v.hidden = !!vq && v.textContent.toLowerCase().indexOf(vq) < 0; });
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
  function openSave() {
    var d = $("#save-dialog"); if (!d) return;
    updateSelCount();
    if (d.showModal && !d.open) d.showModal();
  }

  /* ---------- tabs (company panel, person page) ---------- */
  function selectTab(bar, name) {
    var scope = bar.parentElement;
    $$("[data-tab]", bar).forEach(function (a) { a.setAttribute("aria-selected", String(a.getAttribute("data-tab") === name)); });
    $$("[data-panel]", scope).forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== name; });
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
      openPanel(el.getAttribute("data-open-panel")); return;
    }
    if (t.closest("[data-close-panel]")) { closePanels(); return; }
    if ((el = t.closest("[data-open-dialog]"))) {
      var menu = el.closest("details.dd"); if (menu) menu.open = false;
      var id = el.getAttribute("data-open-dialog");
      if (id === "save-dialog") updateSelCount();
      if (id === "allf-dialog") { applyPins(); refreshCount(0); }
      if (id === "savesearch-dialog") { var nm = $("#ss-name"); if (nm) setTimeout(function () { nm.focus(); }, 30); }
    }
    if (t.closest("[data-col-reset]")) {
      var c = chooser(); if (c) { lsDel(c.getAttribute("data-key")); applyCols(); }
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
    if ((el = t.closest("[data-copy]"))) {
      var text = el.getAttribute("data-copy");
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
    if (panel && !panel.hidden && !t.closest("#col-chooser") && !t.closest("[data-open-panel]") && !t.closest("dialog")) panel.hidden = true;
  });

  doc.addEventListener("change", function (e) {
    var t = e.target; if (!t.matches) return;
    if (t.closest("#allf") && !t.matches("[data-list-search], [data-allf-search], [data-type-select]")) refreshCount();
    if (t.matches("[data-col-toggle]")) saveCols();
    else if (t.matches("[data-page-select]")) {
      var url = t.value;
      if (window.htmx) {
        window.htmx.ajax("GET", url, { target: "#workspace", select: "#workspace", swap: "outerHTML" })
          .then(function () { history.pushState({}, "", url); });
      } else location.href = url;
    } else if (t.matches("[data-type-select]")) setType(t.value);
    else if (t.matches('input[name="list_id"]') && t.getAttribute("data-new-radio") === null) {
      var nn = $("#sd-new"); if (nn) nn.value = "";
    } else if (t.classList.contains("rowcb") || t.classList.contains("allcb")) updateSelCount();
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
    if (el.closest("#save-dialog")) {
      var hdr = (e.detail.xhr && e.detail.xhr.getResponseHeader("HX-Trigger")) || "";
      if (hdr.indexOf('"error"') >= 0) { /* nothing was saved: keep the window open */
        var sd = $("#save-dialog"); if (sd && !sd.open) sd.showModal();
        return;
      }
      clearSelection();
      /* re-render the results so "Saved" chips, counts and the list sizes are current */
      if (window.htmx) window.htmx.ajax("GET", location.pathname + location.search, { target: "#workspace", select: "#workspace", swap: "outerHTML" });
    }
  });

  function syncFiltersLabel() {
    setTimeout(function () {
      var ws = $(".ws");
      root.classList.toggle("filters-off", !!(ws && ws.classList.contains("filters-hidden")));
    }, 0);
  }

  function init() {
    applyPrefs(); applyCols(); applyPins(); restoreOpenGroups(); initTabs(); updateSelCount(); syncFiltersLabel();
  }
  doc.addEventListener("htmx:afterSettle", init);
  restorePerPage();
  init();
})();
