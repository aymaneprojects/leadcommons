/* Win deals: dialogs, company/person typeahead, board drag and drop, copy buttons.
   Every control also works as a plain form without this file (see docs/pipeline.md). */
"use strict";
(function () {
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function toast(message, kind) {
    if (doc.body) doc.body.dispatchEvent(new CustomEvent("toast", { detail: { message: message, kind: kind || "ok" } }));
  }

  /* ---------------------------------------------------------------- dialogs */
  function openDialogs(root) {
    $$("dialog[data-autoopen]:not([data-pl-opened])", root).forEach(function (d) {
      d.setAttribute("data-pl-opened", "1");
      if (d.showModal && !d.open) d.showModal();
      d.addEventListener("close", function () {
        var host = d.closest("[data-pl-host]") || d;
        setTimeout(function () { if (host.parentNode) host.parentNode.removeChild(host); }, 0);
      });
      var first = $("[autofocus], input[type=text]:not([type=hidden])", d);
      if (first) setTimeout(function () { try { first.focus(); } catch (e) { /* ignore */ } }, 30);
    });
  }
  doc.addEventListener("htmx:afterSwap", function (e) { openDialogs(e.detail && e.detail.target ? e.detail.target : doc); });
  doc.addEventListener("htmx:load", function (e) { if (e.target && e.target.querySelectorAll) openDialogs(e.target); });

  function loadDialog(url, trigger) {
    var headers = { "HX-Request": "true", "HX-Current-URL": location.href };
    if (trigger) trigger.setAttribute("aria-busy", "true");
    return fetch(url, { headers: headers, credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (html) {
        var host = doc.createElement("div");
        host.setAttribute("data-pl-host", "1");
        host.innerHTML = html;
        doc.body.appendChild(host);
        if (window.htmx) window.htmx.process(host);
        var dlg = $("dialog", host);
        if (dlg) {
          if (!dlg.hasAttribute("data-autoopen")) dlg.setAttribute("data-autoopen", "");
          openDialogs(host);
        } else {
          host.parentNode.removeChild(host);
          toast("Nothing to show here yet.", "info");
        }
      })
      .catch(function () { toast("Could not open that dialog. Is the feature available?", "error"); })
      .then(function () { if (trigger) trigger.removeAttribute("aria-busy"); });
  }

  /* Escape closes the topmost dialog only; app.js would also clear the drawer */
  window.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && $("dialog[open]")) e.stopPropagation();
  }, true);

  /* ---------------------------------------------------------------- clicks */
  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : e.target.parentElement;
    if (!t) return;

    var dl = t.closest("[data-pl-dialog]");
    if (dl) { e.preventDefault(); loadDialog(dl.getAttribute("data-pl-dialog"), dl); return; }

    var cp = t.closest("[data-copy-from]");
    if (cp) {
      var src = $(cp.getAttribute("data-copy-from"));
      if (src && src.value) copyText(src.value, src);
      return;
    }

    var item = t.closest(".pl-pick-item");
    if (item) { choosePick(item); return; }
    var clr = t.closest("[data-pick-clear]");
    if (clr) { clearPick(clr.closest("[data-pick]")); return; }

    if (!t.closest("[data-pick]")) $$("[data-pick-results]").forEach(function (r) { r.hidden = true; });

    if (t.closest("[data-close-drawer]")) maybeReload();
  });

  function copyText(text, input) {
    function ok() { toast("Link copied."); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(ok, fallback);
    } else { fallback(); }
    function fallback() {
      try { input.select(); doc.execCommand("copy"); ok(); } catch (err) { toast("Press Ctrl+C to copy.", "info"); }
    }
  }

  /* ---------------------------------------------------------------- typeahead */
  var timers = new WeakMap();
  function pickRoot(el) { return el.closest("[data-pick]"); }
  function setPicked(root, id, label) {
    $("[data-pick-id]", root).value = id || "";
    var inp = $("[data-pick-input]", root);
    if (label !== undefined && label !== null) inp.value = label;
    root.classList.toggle("picked", !!id);
    var clr = $("[data-pick-clear]", root);
    if (clr) clr.hidden = !(id || inp.value);
  }
  function choosePick(item) {
    var root = pickRoot(item); if (!root) return;
    setPicked(root, item.getAttribute("data-id"), item.getAttribute("data-label"));
    $("[data-pick-results]", root).hidden = true;
    var cid = item.getAttribute("data-company-id");
    if (cid && root.getAttribute("data-kind") === "person") {
      var scope = root.closest("form") || doc;
      var co = $("[data-pick][data-kind=company]", scope);
      if (co && !$("[data-pick-id]", co).value && !$("[data-pick-input]", co).value.trim()) {
        setPicked(co, cid, item.getAttribute("data-company-name"));
      }
    }
  }
  function clearPick(root) {
    if (!root) return;
    setPicked(root, "", "");
    $("[data-pick-results]", root).hidden = true;
    $("[data-pick-input]", root).focus();
  }
  doc.addEventListener("input", function (e) {
    var inp = e.target.closest ? e.target.closest("[data-pick-input]") : null;
    if (!inp) return;
    var root = pickRoot(inp);
    $("[data-pick-id]", root).value = "";          // typing detaches the chosen record
    root.classList.remove("picked");
    var clr = $("[data-pick-clear]", root); if (clr) clr.hidden = !inp.value;
    clearTimeout(timers.get(inp));
    timers.set(inp, setTimeout(function () { search(root, inp); }, 180));
  });
  function search(root, inp) {
    var q = inp.value.trim(), box = $("[data-pick-results]", root);
    if (q.length < 2) { box.hidden = true; return; }
    fetch("/partials/pick?kind=" + encodeURIComponent(root.getAttribute("data-kind")) + "&q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        if (inp.value.trim() !== q) return;          // a newer keystroke wins
        box.innerHTML = html; box.hidden = false;
      })
      .catch(function () { box.hidden = true; });
  }
  doc.addEventListener("keydown", function (e) {
    var inp = e.target.closest ? e.target.closest("[data-pick-input]") : null;
    if (inp) {
      var root = pickRoot(inp), box = $("[data-pick-results]", root);
      if (e.key === "ArrowDown") { var f = $(".pl-pick-item", box); if (f) { e.preventDefault(); f.focus(); } }
      else if (e.key === "Escape" && !box.hidden) { box.hidden = true; e.stopPropagation(); }
      return;
    }
    var it = e.target.closest ? e.target.closest(".pl-pick-item") : null;
    if (it) {
      if (e.key === "ArrowDown" && it.nextElementSibling && it.nextElementSibling.focus) { e.preventDefault(); it.nextElementSibling.focus(); }
      else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (it.previousElementSibling) it.previousElementSibling.focus(); else $("[data-pick-input]", pickRoot(it)).focus();
      }
    }
  });
  doc.addEventListener("htmx:load", function (e) {
    $$("[data-pick]", e.target).forEach(function (r) { if ($("[data-pick-id]", r).value) r.classList.add("picked"); });
  });

  /* ---------------------------------------------------------------- small form helpers */
  doc.addEventListener("change", function (e) {
    var s = e.target.closest ? e.target.closest("[data-stage-prob]") : null;
    if (s) {
      var o = s.options[s.selectedIndex], form = s.closest("form"), p = form && $("[data-prob-input]", form);
      if (o && p) p.value = o.getAttribute("data-prob") || "0";
    }
  });
  doc.addEventListener("htmx:afterRequest", function (e) {
    var form = e.target;
    if (e.detail.successful && form.matches && form.matches("[data-reset-on-ok]")) form.reset();
  });

  /* ---------------------------------------------------------------- changes made in the drawer */
  var dirty = false;
  doc.addEventListener("pl-changed", function () { dirty = true; });
  function maybeReload() {
    if (dirty && $("[data-pl-board-page]")) { dirty = false; setTimeout(function () { location.reload(); }, 60); }
  }
  doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && !$("dialog[open]")) maybeReload(); });

  /* ---------------------------------------------------------------- deals board */
  var live = function (msg) { var l = $("#pl-live"); if (l) l.textContent = msg; };
  var dragged = null;

  function cardsOf(body) { return $$(".pl-card", body); }
  function refreshEmpty(col) {
    var body = $("[data-dropzone]", col), empty = $("[data-col-empty]", col);
    if (empty) empty.hidden = cardsOf(body).length > 0;
  }
  function applyTotals(totals) {
    Object.keys(totals || {}).forEach(function (id) {
      var col = $('.pl-col[data-stage-id="' + id + '"]');
      if (!col) return;
      var c = $("[data-count]", col), t = $("[data-total]", col);
      if (c) c.textContent = String(totals[id].count);
      if (t) t.textContent = totals[id].amount;
    });
  }
  function postMove(card, stageId, position) {
    var body = new URLSearchParams({ stage_id: stageId, position: String(position) });
    return fetch("/deals/" + encodeURIComponent(card.getAttribute("data-deal-id")) + "/move", {
      method: "POST", body: body, credentials: "same-origin", headers: { Accept: "application/json" }
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok && j.ok, j: j }; }); });
  }
  function commitMove(card, col, index) {
    var prev = card.getAttribute("data-stage-id"), stageId = col.getAttribute("data-stage-id");
    return postMove(card, stageId, index).then(function (res) {
      if (!res.ok) { toast((res.j && res.j.error) || "Could not move the deal.", "error"); location.reload(); return; }
      card.setAttribute("data-stage-id", stageId);
      applyTotals(res.j.totals);
      $$(".pl-col").forEach(refreshEmpty);
      card.classList.add("moved");
      setTimeout(function () { card.classList.remove("moved"); }, 1300);
      var msg = res.j.changed ? "Moved to " + res.j.stage + "." : "Order saved.";
      live(card.getAttribute("aria-label") + ": " + msg);
      if (res.j.changed && prev !== stageId) toast(msg);
    }).catch(function () { toast("Could not move the deal.", "error"); location.reload(); });
  }
  function indexFor(body, y) {
    var cards = cardsOf(body).filter(function (c) { return c !== dragged; });
    for (var i = 0; i < cards.length; i++) {
      var r = cards[i].getBoundingClientRect();
      if (y < r.top + r.height / 2) return { index: i, before: cards[i] };
    }
    return { index: cards.length, before: null };
  }

  doc.addEventListener("dragstart", function (e) {
    var card = e.target.closest ? e.target.closest(".pl-card") : null;
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", card.getAttribute("data-deal-id")); } catch (err) { /* old browsers */ }
  });
  doc.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    $$(".pl-col.drop-over").forEach(function (c) { c.classList.remove("drop-over"); });
  });
  doc.addEventListener("dragover", function (e) {
    if (!dragged) return;
    var col = e.target.closest ? e.target.closest(".pl-col") : null;
    if (!col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    $$(".pl-col.drop-over").forEach(function (c) { if (c !== col) c.classList.remove("drop-over"); });
    col.classList.add("drop-over");
  });
  doc.addEventListener("dragleave", function (e) {
    var col = e.target.closest ? e.target.closest(".pl-col") : null;
    if (col && !col.contains(e.relatedTarget)) col.classList.remove("drop-over");
  });
  doc.addEventListener("drop", function (e) {
    if (!dragged) return;
    var col = e.target.closest ? e.target.closest(".pl-col") : null;
    if (!col) return;
    e.preventDefault();
    col.classList.remove("drop-over");
    var body = $("[data-dropzone]", col), pos = indexFor(body, e.clientY), card = dragged;
    var oldCol = card.closest(".pl-col");
    body.insertBefore(card, pos.before || $("[data-col-empty]", body));
    refreshEmpty(col); if (oldCol) refreshEmpty(oldCol);
    commitMove(card, col, pos.index);
  });

  /* keyboard alternative: Shift + arrows on a focused card */
  doc.addEventListener("keydown", function (e) {
    var card = e.target.closest ? e.target.closest(".pl-card") : null;
    if (!card || e.target !== card || !e.shiftKey) return;
    var col = card.closest(".pl-col"), body = $("[data-dropzone]", col), cols = $$(".pl-col"), i = cols.indexOf(col);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      var target = cols[i + (e.key === "ArrowRight" ? 1 : -1)];
      if (!target) return;
      e.preventDefault();
      var tb = $("[data-dropzone]", target);
      tb.insertBefore(card, $("[data-col-empty]", tb));
      refreshEmpty(col); refreshEmpty(target);
      card.focus();
      commitMove(card, target, cardsOf(tb).length - 1);
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      var cards = cardsOf(body), idx = cards.indexOf(card), to = idx + (e.key === "ArrowDown" ? 1 : -1);
      if (to < 0 || to >= cards.length) return;
      e.preventDefault();
      if (to > idx) body.insertBefore(card, cards[to].nextSibling); else body.insertBefore(card, cards[to]);
      card.focus();
      commitMove(card, col, to);
    }
  });

  /* ---------------------------------------------------------------- stage order (settings) */
  var stageDrag = null;
  doc.addEventListener("mousedown", function (e) {
    var g = e.target.closest ? e.target.closest(".pl-stage-row .pl-grip") : null;
    if (g) g.closest(".pl-stage-row").draggable = true;
  });
  doc.addEventListener("dragstart", function (e) {
    var row = e.target.closest ? e.target.closest(".pl-stage-row") : null;
    if (!row) return;
    stageDrag = row; row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", row.getAttribute("data-stage-id")); } catch (err) { /* ignore */ }
  });
  doc.addEventListener("dragover", function (e) {
    if (!stageDrag) return;
    var row = e.target.closest ? e.target.closest(".pl-stage-row") : null;
    if (!row || row === stageDrag) return;
    e.preventDefault();
    var r = row.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
    row.parentNode.insertBefore(stageDrag, after ? row.nextSibling : row);
  });
  doc.addEventListener("drop", function (e) { if (stageDrag) e.preventDefault(); });
  doc.addEventListener("dragend", function () {
    if (!stageDrag) return;
    var list = stageDrag.closest("#pl-stage-list");
    stageDrag.classList.remove("dragging"); stageDrag.draggable = false; stageDrag = null;
    if (!list) return;
    var body = new URLSearchParams();
    $$(".pl-stage-row", list).forEach(function (r) { body.append("ids", r.getAttribute("data-stage-id")); });
    fetch(list.getAttribute("data-order-url"), { method: "POST", body: body, credentials: "same-origin", headers: { "HX-Request": "true" } })
      .then(function (r) {
        if (r.ok) { toast("Order updated."); location.reload(); } else { toast("Could not save the order.", "error"); }
      })
      .catch(function () { toast("Could not save the order.", "error"); });
  });
})();
