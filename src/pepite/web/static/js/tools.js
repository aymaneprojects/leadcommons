"use strict";
/* Tools and automation: Assistant chat, workflow builder, small helpers. Loaded last. */
(function () {
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function el(tag, attrs, children) {
    var n = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }

  /* ---------------------------------------------------------------- open a partial dialog (sequence picker...) */
  function openPartial(href) {
    var host = $("#lc-dialog-host");
    if (!host || !window.htmx) { location.href = href; return; }
    window.htmx.ajax("GET", href, { target: "#lc-dialog-host", swap: "innerHTML" }).then(function () {
      var d = $("dialog", host);
      if (d && d.showModal && !d.open) d.showModal();
      else if (!d) location.href = href;
    });
  }
  doc.body.addEventListener("lc-open", function (e) { var d = e.detail || {}; if (d.href) openPartial(d.href); });

  /* ---------------------------------------------------------------- copy helpers */
  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : null;
    if (!t) return;
    var copyBtn = t.closest("[data-copy]");
    if (copyBtn) {
      var src = copyBtn.closest(".msg").querySelector("[data-copy-src]");
      copyText(src ? src.innerText : "", copyBtn);
      return;
    }
    var ct = t.closest("[data-copy-text]");
    if (ct) { var n = $(ct.getAttribute("data-copy-text")); copyText(n ? n.textContent : "", ct); }
  });
  function copyText(text, btn) {
    function ok() {
      var label = btn.getAttribute("title") || "";
      btn.setAttribute("title", "Copied");
      if (btn.textContent.trim()) { var old = btn.innerHTML; btn.textContent = "Copied"; setTimeout(function () { btn.innerHTML = old; }, 1500); }
      else setTimeout(function () { btn.setAttribute("title", label); }, 1500);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(ok, ok);
    else {
      var ta = el("textarea", {}); ta.value = text; doc.body.appendChild(ta); ta.select();
      try { doc.execCommand("copy"); } catch (err) { /* ignore */ }
      doc.body.removeChild(ta); ok();
    }
  }

  /* ---------------------------------------------------------------- assistant */
  var chat = $("[data-chat]");
  if (chat) {
    var form = $("#composer"), ta = $("#ask"), log = $("#chat"), thinking = $("#thinking");
    var slash = $("#slash-menu"), cidField = $("[data-cid-field]"), empty = $("#chat-empty");
    function scrollEnd() { window.scrollTo({ top: doc.body.scrollHeight, behavior: "smooth" }); }
    function grow() { ta.style.height = "auto"; ta.style.height = Math.min(180, ta.scrollHeight) + "px"; }
    function slashFilter() {
      var v = ta.value, show = v.charAt(0) === "/" && v.indexOf(" ") < 0, any = false;
      $$(".slash-item", slash).forEach(function (b) {
        var hit = show && b.getAttribute("data-cmd").indexOf(v.toLowerCase()) === 0;
        b.hidden = !hit; if (hit) any = true;
      });
      slash.hidden = !(show && any);
    }
    ta.addEventListener("input", function () { grow(); slashFilter(); });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if ((ta.value.trim() || ($("[data-att-text]") || {}).value) && form.requestSubmit) form.requestSubmit();
      } else if (e.key === "Escape") slash.hidden = true;
    });
    doc.addEventListener("click", function (e) {
      var t = e.target;
      var cmd = t.closest("[data-cmd]");
      if (cmd) { ta.value = cmd.getAttribute("data-cmd"); slash.hidden = true; ta.focus(); grow(); return; }
      if (t.closest("[data-slash-open]")) {
        ta.value = "/"; ta.focus(); slashFilter(); return;
      }
      var st = t.closest("[data-starter]");
      if (st) { ta.value = st.getAttribute("data-starter"); if (ta.value === "/site") { ta.value = "/site "; ta.focus(); slashFilter(); return; } form.requestSubmit(); return; }
      var hb = t.closest("[data-history-toggle]");
      if (hb) { var h = $("#chat-history"); h.hidden = !h.hidden; hb.setAttribute("aria-expanded", String(!h.hidden)); return; }
      if (!t.closest("#chat-history") && !t.closest("[data-history-toggle]")) { var hh = $("#chat-history"); if (hh) hh.hidden = true; }
    });
    form.addEventListener("htmx:beforeRequest", function () {
      thinking.hidden = false; $(".send-btn", form).disabled = true; slash.hidden = true;
    });
    form.addEventListener("htmx:afterRequest", function (e) {
      thinking.hidden = true; $(".send-btn", form).disabled = false;
      if (!e.detail.successful) return;
      var cid = e.detail.xhr.getResponseHeader("X-Conversation");
      if (cid) {
        cidField.value = cid; chat.setAttribute("data-cid", cid);
        var embed = /[?&]embed=1/.test(location.search) ? "?embed=1" : "";
        try { history.replaceState(null, "", "/assistant/" + cid + embed); } catch (err) { /* ignore */ }
      }
      ta.value = ""; grow(); ta.focus();
      if (empty) { empty.hidden = true; }
    });
    log.addEventListener("htmx:afterSwap", scrollEnd);
    doc.addEventListener("htmx:afterSwap", function (e) { if (e.target && e.target.classList && e.target.classList.contains("msg")) scrollEnd(); });
    doc.addEventListener("change", function (e) {
      var r = e.target;
      if (r.matches && r.matches("[data-opts] input[type=radio]")) {
        var b = $("[data-continue]", r.closest("[data-opts]")); if (b) b.disabled = false;
      }
    });
    var auto = chat.getAttribute("data-autosend");
    if (!auto) { var m = location.search.match(/[?&]prompt=([^&]*)/); if (m) auto = decodeURIComponent(m[1].replace(/\+/g, " ")); }
    if (auto) {
      ta.value = auto;
      if (auto === "/site") { ta.value = "/site "; ta.focus(); }
      else setTimeout(function () { form.requestSubmit(); }, 50);
    }
    if ($$(".msg", log).length) setTimeout(function () { window.scrollTo(0, doc.body.scrollHeight); }, 50);
    grow();

    /* welcome option card: pick one, then Continue */
    var wo = $("[data-welcome-opts]");
    if (wo) {
      var go = $("[data-welcome-go]", wo);
      wo.addEventListener("change", function () {
        go.disabled = !$("input:checked", wo);
        if (go.disabled) go.title = "Pick one of the options first"; else go.removeAttribute("title");
      });
      wo.addEventListener("submit", function (e) {
        e.preventDefault();
        var r = $("input:checked", wo); if (!r) return;
        ta.value = r.value;
        if (/^\/site\s*$/.test(ta.value)) { ta.value = "/site "; ta.focus(); slashFilter(); return; }
        form.requestSubmit();
      });
    }

    /* attach a file: a CSV is offered to the enrichment flow, a text file becomes context */
    var att = $("#att"), attIn = $("[data-att-input]"), attName = $("[data-att-name]"), attText = $("[data-att-text]");
    var attLabel = $("[data-att-label]"), attStatus = $("[data-att-status]"), attEnrich = $("[data-att-enrich]");
    var attFile = null;
    function attReset() {
      attFile = null; attIn.value = ""; attName.value = ""; attText.value = "";
      att.hidden = true; attEnrich.hidden = true; attStatus.textContent = ""; attLabel.textContent = "";
      ta.required = true;
    }
    function human(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
    function say(msg) { att.hidden = false; attStatus.textContent = msg; }
    $("[data-attach]").addEventListener("click", function () { attIn.click(); });
    $("[data-att-clear]").addEventListener("click", attReset);
    attIn.addEventListener("change", function () {
      var f = attIn.files && attIn.files[0]; if (!f) return;
      var ext = (f.name.split(".").pop() || "").toLowerCase();
      attReset(); attIn.value = "";
      if (["csv", "txt", "md", "text"].indexOf(ext) < 0) { say("Only .csv, .txt and .md files can be attached."); return; }
      attFile = f; att.hidden = false; attLabel.textContent = f.name + " · " + human(f.size);
      if (ext === "csv") {
        attEnrich.hidden = false;
        say(f.size > 5242880 ? "This file is over 5 MB, the limit for enrichment." : "A CSV can be imported and enriched with emails. Nothing is sent anywhere.");
        return;
      }
      var rd = new FileReader();
      rd.onload = function () {
        attName.value = f.name; attText.value = String(rd.result || "").slice(0, 20000);
        ta.required = false;
        say("Added as context for this chat" + (f.size > 40000 ? " (the first part of the file)." : "."));
      };
      rd.onerror = function () { say("That file could not be read."); };
      rd.readAsText(f.slice(0, 40000));
    });
    attEnrich.addEventListener("click", function () {
      if (attEnrich.getAttribute("aria-disabled") === "true") { say(attEnrich.getAttribute("title")); return; }
      if (!attFile || !window.fetch) return;
      var fd = new FormData(); fd.append("file", attFile);
      attEnrich.disabled = true; say("Uploading…");
      fetch("/api/enrich", { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok || !res.j.job_id) { attEnrich.disabled = false; say(String(res.j.detail || "Upload failed.")); return; }
          say("Enriching… you can keep chatting, the job runs in the background.");
          var timer = setInterval(function () {
            fetch("/api/jobs/" + encodeURIComponent(res.j.job_id), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (j) {
              if (j.status === "done") {
                clearInterval(timer); attEnrich.disabled = false; attStatus.textContent = "Done. ";
                attStatus.appendChild(el("a", { href: "/api/enrich/" + encodeURIComponent(res.j.job_id) + "/result.csv", text: "Download the enriched CSV" }));
              } else if (j.status === "failed") { clearInterval(timer); attEnrich.disabled = false; say("Failed: " + String(j.error || "unknown error")); }
            });
          }, 1500);
        })
        .catch(function () { attEnrich.disabled = false; say("Upload failed."); });
    });
    form.addEventListener("htmx:afterRequest", function (e) { if (e.detail.successful && attText.value) attReset(); });
    form.addEventListener("htmx:configRequest", function () { /* fields are plain inputs: nothing to add */ });

    /* dictation (Web Speech API) */
    var mic = $("[data-mic]"), SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (mic && doc.documentElement.dataset.app === "desktop") {
      /* The desktop app's engine has no speech service: point to the system's own dictation. */
      mic.setAttribute("title", "Use your computer's dictation in the message box: Windows key + H on Windows, Fn twice (or the mic key) on a Mac.");
    } else if (mic && SR) {
      mic.removeAttribute("aria-disabled");
      mic.setAttribute("title", "Dictate. Your browser may send the audio to its own speech service.");
      var rec = null, base = "";
      mic.addEventListener("click", function () {
        if (rec) { rec.stop(); return; }
        rec = new SR(); rec.interimResults = true; rec.continuous = false; rec.lang = doc.documentElement.lang || navigator.language || "en";
        base = ta.value ? ta.value.replace(/\s*$/, " ") : "";
        rec.onresult = function (ev) {
          var out = ""; for (var i = 0; i < ev.results.length; i++) out += ev.results[i][0].transcript;
          ta.value = base + out; grow(); slashFilter();
        };
        rec.onerror = function (ev) { say(ev.error === "not-allowed" ? "Microphone access was blocked in the browser." : "Dictation stopped (" + ev.error + ")."); };
        rec.onend = function () { rec = null; mic.classList.remove("on"); mic.setAttribute("aria-pressed", "false"); ta.focus(); };
        try { rec.start(); mic.classList.add("on"); mic.setAttribute("aria-pressed", "true"); } catch (err) { rec = null; }
      });
    } else if (mic) {
      mic.addEventListener("click", function () { say(mic.getAttribute("title")); });
    }
  }

  /* ---------------------------------------------------------------- workflow builder */
  var cfgNode = $("#wf-config");
  if (cfgNode) {
    var cfg = JSON.parse(cfgNode.textContent);
    var condBox = $("#cond-rows"), actBox = $("#act-rows"), counter = 0;
    function opt(value, label, selected) {
      var o = el("option", { value: value, text: label });
      if (selected) o.selected = true;
      return o;
    }
    function select(name, options, current, cls) {
      var s = el("select", { name: name, "class": cls || "" });
      options.forEach(function (o) { s.appendChild(opt(o[0], o[1], String(o[0]) === String(current))); });
      return s;
    }
    function removeBtn(row) {
      var b = el("button", { type: "button", "class": "btn ghost icon-only", "aria-label": "Remove this row", title: "Remove" }, [svg("x")]);
      b.addEventListener("click", function () { row.remove(); renumber(); });
      return b;
    }
    function svg(name) {
      var s = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
      s.setAttribute("class", "icon sm"); s.setAttribute("aria-hidden", "true");
      var u = doc.createElementNS("http://www.w3.org/2000/svg", "use");
      u.setAttributeNS("http://www.w3.org/1999/xlink", "href", "/static/icons/tools-sprite.svg#t-" + name);
      u.setAttribute("href", "/static/icons/tools-sprite.svg#t-" + name);
      s.appendChild(u); return s;
    }
    function renumber() {
      $$(".wf-row", condBox).forEach(function (r, i) { $(".idx", r).textContent = String(i + 1); });
      $$(".wf-row", actBox).forEach(function (r, i) { $(".idx", r).textContent = String(i + 1); });
    }

    function valueControl(i, field, value) {
      var kind = cfg.fields[field].kind, name = "cond_value_" + i;
      if (kind === "country") return select(name, [["", "Choose…"]].concat(cfg.countries), value);
      if (kind === "size") return select(name, [["", "Choose…"]].concat(cfg.sizes.map(function (s) { return [s, s + " employees"]; })), value);
      if (kind === "seniority") return select(name, [["", "Choose…"]].concat(cfg.seniorities), value);
      if (kind === "status") return select(name, [["", "Choose…"]].concat(cfg.statuses.map(function (s) { return [s, s.replace("_", " ")]; })), value);
      if (kind === "list") return select(name, [["", "Choose…"]].concat(cfg.lists.map(function (l) { return [l.id, l.name + " (" + l.kind + ")"]; })), value);
      return el("input", { type: "text", name: name, value: value || "", placeholder: "Value", required: "required" });
    }
    function addCondition(c) {
      var i = counter++;
      c = c || { field: "country", op: "is", value: "" };
      var row = el("div", { "class": "wf-row" });
      var fields = Object.keys(cfg.fields).map(function (k) { return [k, cfg.fields[k].label]; });
      var fSel = select("cond_field_" + i, fields, c.field);
      var param = el("div", { "class": "param" });
      function build(field, op, value) {
        param.textContent = "";
        param.appendChild(select("cond_op_" + i, cfg.fields[field].ops, op));
        param.appendChild(valueControl(i, field, value));
      }
      fSel.addEventListener("change", function () { build(fSel.value, "", ""); });
      row.appendChild(el("span", { "class": "idx" }));
      row.appendChild(fSel); row.appendChild(param); row.appendChild(removeBtn(row));
      build(c.field, c.op, c.value);
      condBox.appendChild(row); renumber();
    }
    function addAction(a) {
      var i = counter++;
      a = a || { type: "add_to_list" };
      var row = el("div", { "class": "wf-row" });
      var types = Object.keys(cfg.actions).map(function (k) { return [k, cfg.actions[k].label]; });
      var tSel = select("action_type_" + i, types, a.type);
      var param = el("div", { "class": "param" });
      function build(t, a) {
        param.textContent = "";
        if (t === "add_to_list") {
          param.appendChild(select("action_arg_" + i, [["", "Choose a list…"]].concat(cfg.lists.map(function (l) { return [l.id, l.name + " (" + l.kind + ")"]; })), a.list_id));
          if (!cfg.lists.length) param.appendChild(el("span", { "class": "hint", text: "Create a list first (Lists page)." }));
        } else if (t === "enroll") {
          param.appendChild(select("action_arg_" + i, [["", cfg.sequences.length ? "Choose a sequence…" : "No sequence yet"]].concat(cfg.sequences.map(function (s) { return [s.id, s.name]; })), a.sequence_id));
        } else if (t === "create_task") {
          param.appendChild(el("input", { type: "text", name: "action_arg_" + i, value: a.title || "Follow up with {name}", placeholder: "Task title", maxlength: "200" }));
          param.appendChild(select("action_kind_" + i, cfg.taskKinds.map(function (k) { return [k, k]; }), a.kind || "action"));
          param.appendChild(el("input", { type: "number", name: "action_extra_" + i, min: "0", max: "365", value: a.due_days === undefined ? 2 : a.due_days, title: "Due in days", "aria-label": "Due in days" }));
        } else if (t === "webhook") {
          param.appendChild(el("input", { type: "url", name: "action_arg_" + i, value: a.url || "", placeholder: "https://example.com/hook", required: "required" }));
        } else if (t === "notify") {
          param.appendChild(el("input", { type: "text", name: "action_arg_" + i, value: a.message || "Workflow notification for {name}", maxlength: "300" }));
        } else {
          param.appendChild(el("span", { "class": "muted", text: "No setting needed." }));
        }
      }
      tSel.addEventListener("change", function () { build(tSel.value, {}); });
      row.appendChild(el("span", { "class": "idx" }));
      row.appendChild(tSel); row.appendChild(param); row.appendChild(removeBtn(row));
      build(a.type, a);
      actBox.appendChild(row); renumber();
    }
    (cfg.draft.conditions || []).forEach(addCondition);
    (cfg.draft.actions && cfg.draft.actions.length ? cfg.draft.actions : [{ type: "add_to_list" }]).forEach(addAction);
    $("[data-add-cond]").addEventListener("click", function () { addCondition(); });
    $("[data-add-act]").addEventListener("click", function () { addAction(); });

    var fb = $("#wf-form");
    $$("[data-trig]", fb).forEach(function (r) {
      r.addEventListener("change", function () {
        $$(".trig-pane", fb).forEach(function (p) { p.hidden = p.getAttribute("data-pane") !== r.value; });
      });
    });
    var freq = $("#wf-freq"), wd = $("[data-weekday]");
    if (freq) freq.addEventListener("change", function () { wd.hidden = freq.value !== "weekly"; });
    var saved = $("[data-saved]");
    if (saved) {
      cfg.saved.forEach(function (s, k) { saved.appendChild(opt(String(k), s.name + " (" + s.kind + ")")); });
      saved.addEventListener("change", function () {
        var s = cfg.saved[Number(saved.value)];
        if (!s) return;
        $("#wf-source").value = s.kind; $("#wf-query").value = s.query;
      });
    }
  }
})();
