/* Engage: dialogs, live search, previews, variable chips and small form helpers.
   Every control also works without this file: links open the dialog as a page and forms post. */
"use strict";
(function () {
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function toast(message, kind) {
    if (doc.body) doc.body.dispatchEvent(new CustomEvent("toast", { detail: { message: message, kind: kind || "ok" } }));
  }
  function get(url) {
    return fetch(url, { credentials: "same-origin", headers: { "HX-Request": "true", "HX-Current-URL": location.href } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); });
  }
  function debounce(fn, ms) {
    var t; return function () { var a = arguments, self = this; clearTimeout(t); t = setTimeout(function () { fn.apply(self, a); }, ms); };
  }

  /* ---------------------------------------------------------------- dialogs */
  function wire(dlg) {
    if (dlg.getAttribute("data-eg-wired")) return;
    dlg.setAttribute("data-eg-wired", "1");
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
    $$("[data-eg-load]", dlg).forEach(function (el) { load(el, el.getAttribute("data-eg-load")); });
    countSelected(dlg);
  }
  function openDialog(dlg) {
    wire(dlg);
    if (dlg.showModal && !dlg.open) dlg.showModal();
    var first = $("[autofocus]", dlg) || $("input[type=text]:not([disabled]), input[type=email]:not([disabled]), textarea", dlg);
    if (first) setTimeout(function () { try { first.focus(); } catch (err) { /* ignore */ } }, 30);
  }
  function loadDialog(url, trigger) {
    if (trigger) trigger.setAttribute("aria-busy", "true");
    get(url).then(function (html) {
      var host = doc.createElement("div");
      host.setAttribute("data-eg-host", "");
      host.innerHTML = html;
      doc.body.appendChild(host);
      if (window.htmx) window.htmx.process(host);
      var dlg = $("dialog", host);
      if (!dlg) { host.remove(); toast("Nothing to show here.", "info"); return; }
      dlg.setAttribute("data-pl-opened", "1"); /* pipeline.js: already handled here */
      dlg.addEventListener("close", function () { setTimeout(function () { host.remove(); }, 0); });
      openDialog(dlg);
    }).catch(function (status) {
      toast(status === 404 ? "This is not available." : "Could not open it (" + status + ").", "error");
    }).then(function () { if (trigger) trigger.removeAttribute("aria-busy"); });
  }
  /* dialogs inserted by other features' loaders */
  doc.addEventListener("htmx:load", function (e) {
    var root = e.target && e.target.querySelectorAll ? e.target : null;
    if (!root) return;
    $$("dialog[data-eg-dlg]", root).forEach(wire);
    if (root.matches && root.matches("dialog[data-eg-dlg]")) wire(root);
  });
  /* the new-sequence dialog lives in the page: preselect a template */
  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : e.target.parentElement;
    if (!t) return;
    var pick = t.closest("[data-eg-pick]");
    if (pick) {
      var radio = $('#eg-new-seq input[name=template][value="' + pick.getAttribute("data-eg-pick") + '"]');
      if (radio) radio.checked = true;
      var nd = $("#eg-new-seq"); if (nd) setTimeout(function () { wire(nd); var n = $("#eg-seq-name"); if (n) n.focus(); }, 20);
    }
    var opener = t.closest("[data-eg-dialog]");
    if (opener) {
      e.preventDefault();
      var dd = opener.closest("details.dd"); if (dd) dd.open = false;
      loadDialog(opener.getAttribute("data-eg-dialog"), opener);
      return;
    }
    var ins = t.closest("[data-eg-insert]");
    if (ins) { insertVar(ins); return; }
    var tog = t.closest("[data-eg-toggle-el]");
    if (tog) {
      var el = $(tog.getAttribute("data-eg-toggle-el"));
      if (el) {
        el.hidden = !el.hidden;
        tog.setAttribute("aria-expanded", String(!el.hidden));
        var lab = $("span", tog); if (lab) lab.textContent = el.hidden ? "Show filters" : "Hide filters";
      }
      return;
    }
    var fill = t.closest("[data-eg-fill]");
    if (fill) { var inp = $(fill.getAttribute("data-eg-fill")); if (inp) { inp.value = fill.getAttribute("data-eg-value"); inp.focus(); } return; }
    var prev = t.closest("[data-eg-preview-step]");
    if (prev) {
      var sel = $("[data-eg-preview-stepsel]");
      if (sel) { sel.value = prev.getAttribute("data-eg-preview-step"); refreshPreview(); }
      var pane = $("[data-eg-preview]"); if (pane && window.innerWidth < 1100) pane.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  /* ---------------------------------------------------------------- variables */
  var lastField = null;
  doc.addEventListener("focusin", function (e) {
    var t = e.target;
    if (t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && t.type === "text")) && t.closest("form")) lastField = t;
  });
  function insertVar(btn) {
    var text = btn.getAttribute("data-eg-insert");
    var target = doc.getElementById(btn.getAttribute("data-eg-target"));
    var form = btn.closest("form");
    if (lastField && form && form.contains(lastField) && lastField.offsetParent !== null) target = lastField;
    if (!target) return;
    var s = target.selectionStart == null ? target.value.length : target.selectionStart;
    var en = target.selectionEnd == null ? s : target.selectionEnd;
    target.value = target.value.slice(0, s) + text + target.value.slice(en);
    target.focus();
    target.setSelectionRange(s + text.length, s + text.length);
    target.dispatchEvent(new Event("input", { bubbles: true }));
  }

  /* ---------------------------------------------------------------- forms */
  doc.addEventListener("submit", function (e) {
    var f = e.target;
    var msg = f.getAttribute && f.getAttribute("data-eg-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  doc.addEventListener("change", function (e) {
    var t = e.target;
    if (t.hasAttribute && t.hasAttribute("data-eg-pref")) { savePrefs(); applyPrefs(); return; }
    var auto = t.closest && t.closest("form[data-eg-autosubmit]");
    if (auto && (t.tagName === "SELECT" || t.type === "checkbox" || t.type === "radio")) { auto.requestSubmit ? auto.requestSubmit() : auto.submit(); return; }
    if (t.hasAttribute && t.hasAttribute("data-eg-toggle")) {
      var target = $(t.getAttribute("data-eg-toggle"));
      if (target) target.hidden = t.hasAttribute("data-eg-toggle-off");
    }
    if (t.matches && t.matches("[data-eg-provider]")) applyProvider(t);
    if (t.matches && (t.matches("[data-eg-preview-person]") || t.matches("[data-eg-preview-stepsel]"))) refreshPreview();
    if (t.name === "ids") countSelected(t.closest("dialog") || doc);
  });
  var submitSearch = debounce(function (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }, 450);
  doc.addEventListener("input", function (e) {
    var t = e.target;
    if (t.type === "search" && t.closest("form[data-eg-autosubmit]")) { submitSearch(t.closest("form[data-eg-autosubmit]")); return; }
    if (t.hasAttribute("data-eg-filter")) {
      var q = t.value.trim().toLowerCase();
      $$(t.getAttribute("data-eg-filter"), t.closest("dialog") || doc).forEach(function (row) {
        row.hidden = q && (row.getAttribute("data-eg-text") || "").indexOf(q) < 0;
      });
      return;
    }
    if (t.hasAttribute("data-eg-live")) { liveSearch(t); return; }
    var rn = t.closest("[data-eg-rename]");
    if (rn) rn.classList.toggle("dirty", t.value !== t.defaultValue);
  });
  doc.addEventListener("focusin", function (e) {
    var t = e.target;
    if (t.hasAttribute && t.hasAttribute("data-eg-check-parent")) {
      var r = t.closest("label") && $("input[type=radio]", t.closest("label"));
      if (r) r.checked = true;
    }
  });
  doc.addEventListener("focusout", function (e) {
    var t = e.target;
    if (t.matches && t.matches("[data-eg-copy-to]")) {
      var to = $(t.getAttribute("data-eg-copy-to"));
      if (to && !to.value) to.value = t.value;
    }
  });

  /* ---------------------------------------------------------------- live people search */
  function load(el, url) {
    get(url).then(function (html) { el.innerHTML = html; countSelected(el.closest("dialog") || doc); })
      .catch(function () { el.innerHTML = '<p class="eg-dim">Could not load the list.</p>'; });
  }
  var liveTimers = new WeakMap();
  function liveSearch(input) {
    clearTimeout(liveTimers.get(input));
    liveTimers.set(input, setTimeout(function () {
      var target = $(input.getAttribute("data-eg-live-target"));
      if (!target) return;
      var url = input.getAttribute("data-eg-live");
      load(target, url + (url.indexOf("?") >= 0 ? "&" : "?") + "q=" + encodeURIComponent(input.value.trim()));
    }, 300));
  }
  function countSelected(root) {
    if (!root) return;
    var out = $("[data-eg-selcount]", root);
    if (out) out.textContent = $$("input[name=ids]:checked", root).length + " selected";
  }

  /* ---------------------------------------------------------------- step preview */
  function refreshPreview() {
    var pane = $("[data-eg-preview]"); if (!pane) return;
    var body = $("[data-eg-preview-body]", pane);
    var step = $("[data-eg-preview-stepsel]", pane), person = $("[data-eg-preview-person]", pane);
    if (!step || !step.value) return;
    $$(".eg-step.sel").forEach(function (s) { s.classList.remove("sel"); });
    var card = doc.getElementById("step-" + step.value); if (card) card.classList.add("sel");
    var url = "/sequences/" + pane.getAttribute("data-seq") + "/preview?step_id=" + encodeURIComponent(step.value) +
      "&person_id=" + encodeURIComponent(person ? person.value : "");
    get(url).then(function (html) { body.innerHTML = html; localizeTimes(body); })
      .catch(function () { body.innerHTML = '<p class="eg-dim">The preview is not available.</p>'; });
  }

  /* ---------------------------------------------------------------- mailbox form */
  function applyProvider(sel) {
    var opt = sel.options[sel.selectedIndex]; if (!opt) return;
    var smtp = (opt.getAttribute("data-smtp") || "").split("|"), imap = (opt.getAttribute("data-imap") || "").split("|");
    function put(group, parts) {
      var keys = ["host", "port", "security"];
      keys.forEach(function (k, i) {
        var el = $("[data-eg-" + group + '="' + k + '"]');
        if (el && parts[i] !== undefined && (parts[i] !== "" || k !== "host" || sel.value === "custom")) el.value = parts[i];
      });
    }
    put("smtp", smtp); put("imap", imap);
    var hint = $("[data-eg-provider-hint]"); if (hint) hint.textContent = opt.getAttribute("data-hint") || "";
  }
  function defaultTimezone() {
    var sel = $('select[data-eg-tz-default="1"]'); if (!sel) return;
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (tz && $$("option", sel).some(function (o) { return o.value === tz || o.textContent === tz; })) sel.value = tz;
    } catch (err) { /* keep the server default */ }
  }

  /* ---------------------------------------------------------------- task view options (per viewer) */
  var PREFS = "lc-eg-task-view";
  function readPrefs() { try { return JSON.parse(localStorage.getItem(PREFS) || "{}"); } catch (err) { return {}; } }
  function savePrefs() {
    var out = {};
    $$("[data-eg-pref]").forEach(function (b) { out[b.getAttribute("data-eg-pref")] = b.checked; });
    try { localStorage.setItem(PREFS, JSON.stringify(out)); } catch (err) { /* private window */ }
  }
  function applyPrefs() {
    var table = $("[data-eg-prefs-target]"), prefs = readPrefs();
    $$("[data-eg-pref]").forEach(function (b) {
      var k = b.getAttribute("data-eg-pref");
      if (Object.prototype.hasOwnProperty.call(prefs, k)) b.checked = !!prefs[k];
      if (!table) return;
      if (k === "compact") table.classList.toggle("eg-compact", b.checked);
      else table.classList.toggle("hide-" + k.replace("col-", ""), !b.checked);
    });
  }

  /* ---------------------------------------------------------------- times in local time */
  function localizeTimes(root) {
    var fmt;
    try { fmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); } catch (err) { return; }
    $$("time[data-eg-time]", root).forEach(function (el) {
      var d = new Date(el.getAttribute("datetime")); if (isNaN(d)) return;
      el.title = fmt.format(d);
      if (!el.hasAttribute("data-eg-rel")) el.textContent = fmt.format(d);
    });
  }

  function init() {
    localizeTimes(doc);
    defaultTimezone();
    applyPrefs();
    refreshPreview();
    var nd = $("#eg-new-seq"); if (nd) wire(nd);
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();
  doc.addEventListener("htmx:afterSettle", function (e) { localizeTimes(e.target || doc); });
})();
