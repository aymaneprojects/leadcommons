/* Pépite UI behaviour. Everything degrades gracefully without JS. */
(function () {
  "use strict";
  var doc = document;
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, val);
    } catch (e) { /* storage unavailable */ }
    return null;
  }
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }

  /* ---- sidebar collapse + groups ---- */
  function setCollapsed(on) {
    doc.documentElement.classList.toggle("nav-collapsed", on);
    var b = $("[data-collapse]");
    if (b) b.setAttribute("aria-expanded", String(!on));
    store("lc-nav", on ? "collapsed" : "open");
  }
  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : e.target.parentElement;
    var c = t.closest("[data-collapse]");
    if (c) { setCollapsed(!doc.documentElement.classList.contains("nav-collapsed")); return; }
    var g = t.closest("[data-group]");
    if (g) {
      var open = g.getAttribute("aria-expanded") !== "true";
      g.setAttribute("aria-expanded", String(open));
      store("lc-group-" + g.getAttribute("data-group"), open ? "1" : "0");
      return;
    }
    if (t.closest("[data-theme-toggle]")) {
      var dark = doc.documentElement.getAttribute("data-theme") !== "light";
      doc.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
      store("lc-theme", dark ? "light" : "dark");
      return;
    }
    var od = t.closest("[data-open-dialog]");
    if (od) { var d = doc.getElementById(od.getAttribute("data-open-dialog")); if (d && d.showModal) d.showModal(); return; }
    if (t.closest(".close-dlg")) { var dd = t.closest("dialog"); if (dd && dd.close) dd.close(); return; }
    if (t.closest("[data-close-drawer]")) { closeDrawer(); return; }
    if (t.closest("[data-toggle-filters]")) {
      var ws = $(".ws"); if (ws) { var h = ws.classList.toggle("filters-hidden"); store("lc-filters-hidden", h ? "1" : "0"); }
      return;
    }
    if (t.closest("[data-jobs-toggle]")) { toggleJobs(); return; }
    if (!t.closest(".jobs")) { var m = $("#jobs-menu"); if (m) m.hidden = true; }
    $$("details.dd[open]").forEach(function (dd2) { if (!dd2.contains(t)) dd2.open = false; });
    if (t.closest("[data-palette-open]")) { openPalette(); }
  });

  function applyGroups() {
    $$("[data-group]").forEach(function (g) {
      var v = store("lc-group-" + g.getAttribute("data-group"));
      if (v === "0") g.setAttribute("aria-expanded", "false");
    });
  }

  /* ---- toasts ---- */
  function toast(message, kind) {
    var area = $("#toasts"); if (!area || !message) return;
    var el = doc.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = message;
    area.appendChild(el);
    setTimeout(function () { el.remove(); }, 4500);
  }
  doc.body.addEventListener("toast", function (e) {
    var d = e.detail || {};
    toast(d.message || d.value, d.kind);
  });
  doc.body.addEventListener("htmx:responseError", function (e) {
    if (e.detail && e.detail.xhr && e.detail.xhr.status >= 400 && !(e.detail.elt && e.detail.elt.closest && e.detail.elt.closest("[data-quiet]"))) {
      toast("Something went wrong (" + e.detail.xhr.status + ").", "error");
    }
  });

  /* ---- drawer ---- */
  function closeDrawer() { var d = $("#drawer"); if (d) d.innerHTML = ""; }
  doc.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
    else if (e.key === "Escape") { closeDrawer(); }
  });

  /* ---- jobs dropdown ---- */
  function toggleJobs() {
    var m = $("#jobs-menu"); if (!m) return;
    m.hidden = !m.hidden;
    $("[data-jobs-toggle]").setAttribute("aria-expanded", String(!m.hidden));
    if (!m.hidden && window.htmx) window.htmx.trigger(m, "load-jobs");
  }

  /* ---- row selection + bulk bar ---- */
  function refreshSelection() {
    var boxes = $$("input.rowcb");
    var n = 0;
    boxes.forEach(function (b) {
      if (b.checked) n++;
      var tr = b.closest("tr"); if (tr) tr.classList.toggle("sel", b.checked);
    });
    var bar = $(".bulk");
    if (bar) {
      bar.classList.toggle("active", n > 0);
      var c = $(".bulk .count", bar); if (c) c.textContent = n + " selected";
    }
    var all = $("input.allcb");
    if (all) { all.checked = n > 0 && n === boxes.length; all.indeterminate = n > 0 && n < boxes.length; }
  }
  doc.addEventListener("change", function (e) {
    var t = e.target;
    if (t.classList && t.classList.contains("allcb")) {
      $$("input.rowcb").forEach(function (b) { b.checked = t.checked; });
      refreshSelection();
    } else if (t.classList && t.classList.contains("rowcb")) {
      refreshSelection();
    }
  });
  doc.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-save-search-form]");
    if (b) return;
  });
  doc.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.matches && f.matches("[data-save-search-form]")) {
      var q = $("[name=query]", f), nx = $("[name=next]", f);
      if (q) q.value = location.search.replace(/^\?/, "");
      if (nx) nx.value = location.pathname + location.search;
    }
  }, true);
  doc.addEventListener("htmx:afterRequest", function (e) {
    var el = e.target;
    if (e.detail.successful && el.closest && el.closest("dialog") && el.closest("dialog").id !== "palette") {
      el.closest("dialog").close();
    }
  });
  /* keep shareable URLs clean: drop empty and default params from GET filter requests */
  doc.body.addEventListener("htmx:configRequest", function (e) {
    var d = e.detail;
    if (String(d.verb).toLowerCase() !== "get" || !d.elt || d.elt.id !== "f") return;
    var drop = [];
    Object.keys(d.parameters).forEach(function (k) {
      var v = d.parameters[k];
      if (v === "" || (k === "sort" && v === "relevance") || (k === "tab" && v === "all")) drop.push(k);
    });
    drop.forEach(function (k) { delete d.parameters[k]; });
  });
  doc.addEventListener("htmx:afterSettle", function () {
    refreshSelection(); applyFilterVisibility();
  });
  function applyFilterVisibility() {
    var ws = $(".ws"); if (ws && store("lc-filters-hidden") === "1") ws.classList.add("filters-hidden");
  }

  /* ---- command palette ---- */
  var PAGES = [
    ["Home", "/", "house"], ["Assistant", "/assistant", "sparkles"], ["People", "/people", "users"],
    ["Companies", "/companies", "building-2"], ["Lists", "/lists", "list"], ["Enrichment", "/enrich", "file-up"],
    ["Settings", "/settings", "settings"], ["Sequences (soon)", "/sequences", "send"], ["Emails (soon)", "/emails", "mail"],
    ["Tasks (soon)", "/tasks", "square-check-big"], ["Deals (soon)", "/deals", "handshake"], ["Privacy and opt-out", "/opt-out", "eye-off"]
  ];
  var sprite = (doc.documentElement.getAttribute("data-sprite") || "/static/icons/sprite.svg");
  function ico(name) {
    return '<svg class="icon" aria-hidden="true"><use href="' + sprite + "#i-" + name + '"></use></svg>';
  }
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  var palIdx = 0;
  function renderPalette() {
    var inp = $("#palette-input"), ul = $("#palette-list"); if (!inp || !ul) return;
    var q = inp.value.trim(), ql = q.toLowerCase();
    var items = PAGES.filter(function (p) { return !ql || p[0].toLowerCase().indexOf(ql) >= 0; })
      .map(function (p) { return { label: "Go to " + p[0], href: p[1], icon: p[2] }; });
    if (q) {
      items.unshift({ label: "Search companies for “" + q + "”", href: "/companies?q=" + encodeURIComponent(q), icon: "search" });
      items.push({ label: "Search people for “" + q + "”", href: "/people?q=" + encodeURIComponent(q), icon: "users" });
    }
    palIdx = Math.min(palIdx, Math.max(items.length - 1, 0));
    ul.innerHTML = items.map(function (it, i) {
      return '<li><a href="' + esc(it.href) + '" class="' + (i === palIdx ? "on" : "") + '">' + ico(it.icon) + esc(it.label) + "</a></li>";
    }).join("");
  }
  function openPalette() {
    var d = $("#palette"); if (!d || !d.showModal) return;
    if (!d.open) d.showModal();
    var inp = $("#palette-input"); inp.value = ""; palIdx = 0; renderPalette(); inp.focus();
  }
  var pin = $("#palette-input");
  if (pin) {
    pin.addEventListener("input", function () { palIdx = 0; renderPalette(); });
    pin.addEventListener("keydown", function (e) {
      var n = $$("#palette-list a").length;
      if (e.key === "ArrowDown") { e.preventDefault(); palIdx = (palIdx + 1) % Math.max(n, 1); renderPalette(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); palIdx = (palIdx - 1 + n) % Math.max(n, 1); renderPalette(); }
      else if (e.key === "Enter") { e.preventDefault(); var a = $$("#palette-list a")[palIdx]; if (a) location.href = a.getAttribute("href"); }
    });
    var pd = $("#palette");
    pd.addEventListener("click", function (e) { if (e.target === pd) pd.close(); });
  }
  var ts = $("#topsearch-input");
  if (ts) {
    ts.addEventListener("focus", function () { ts.blur(); openPalette(); });
    ts.closest("form").addEventListener("submit", function (e) { e.preventDefault(); openPalette(); });
  }

  /* ---- CSV enrichment flow ---- */
  var ef = $("#enrich-form");
  if (ef && window.fetch) {
    ef.addEventListener("submit", function (e) {
      e.preventDefault();
      var status = $("#enrich-status"), file = $("input[type=file]", ef);
      if (!file.files.length) { status.textContent = "Choose a CSV file first."; return; }
      status.innerHTML = '<span class="dot amber pulse"></span> Uploading…';
      var fd = new FormData(); fd.append("file", file.files[0]);
      fetch("/api/enrich", { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok || !res.j.job_id) { status.innerHTML = '<span class="dot red"></span> ' + esc(String(res.j.detail || "Upload failed.")); return; }
          poll(res.j.job_id);
        })
        .catch(function () { status.innerHTML = '<span class="dot red"></span> Upload failed.'; });
    });
  }
  function poll(id) {
    var status = $("#enrich-status");
    status.innerHTML = '<span class="dot ok pulse"></span> Enriching… you can leave this page, the job keeps running.';
    var timer = setInterval(function () {
      fetch("/api/jobs/" + encodeURIComponent(id), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.status === "done") {
            clearInterval(timer);
            status.innerHTML = '<span class="dot ok"></span> Done. <a class="btn primary sm" href="/api/enrich/' + encodeURIComponent(id) + '/result.csv">Download enriched CSV</a>';
          } else if (j.status === "failed") {
            clearInterval(timer);
            status.innerHTML = '<span class="dot red"></span> Failed: ' + esc(String(j.error || "unknown error"));
          }
        });
    }, 1500);
  }

  doc.documentElement.classList.add("js");
  applyGroups(); applyFilterVisibility(); refreshSelection();
})();
