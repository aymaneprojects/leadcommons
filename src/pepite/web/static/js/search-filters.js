/* Search filters (People, Companies): comboboxes with suggestions, reveal boxes, size modes,
   open-state memory across HTMX swaps, pinned-filter order with drag handles, and the Engagement
   column of the All filters window. Loaded after search.js, which owns pins on/off, the live
   count and the filter-name search. Without JavaScript every control is a plain form field. */
"use strict";
(function () {
  if (window.lcSearchFilters) return;
  window.lcSearchFilters = true;
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function fire(el, type) { if (el) el.dispatchEvent(new Event(type, { bubbles: true })); }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage off */ } }
  function kind() { var w = $("#workspace"); return w ? w.getAttribute("data-kind") : ""; }

  /* Ask the form to apply itself: a change event on one of its own fields (htmx listens for it
     on #f; search.js refreshes the live count on #allf). */
  function ping(form) {
    if (!form) return;
    fire(form.querySelector("input[type=hidden][name]") || form.querySelector("input[name], select[name]"), "change");
  }

  /* ---------------------------------------------------------------- comboboxes */
  var FREE_ADD = /^x?(place|industry)$/; /* free text the server resolves (country or place, ISIC) */
  var timer = null, seq = 0, open = null; /* open = {combo, items, active} */

  function comboOf(el) { return el && el.closest ? el.closest("[data-combo]") : null; }
  function inputOf(combo) { return $(".sf-combo-input", combo); }
  function listOf(combo) { return $(".sf-combo-list", combo); }
  function chosen(combo) {
    return $$("[data-chip] input[type=hidden]", combo).map(function (h) { return h.name + "\u0000" + h.value.toLowerCase(); });
  }
  function syncCombo(combo) {
    var has = $$("[data-chip]", combo).length > 0, input = inputOf(combo), clear = $("[data-combo-clear]", combo);
    if (clear) clear.hidden = !has;
    if (input) input.placeholder = has ? "" : (input.getAttribute("data-placeholder") || "");
  }
  function addChip(combo, name, value, text) {
    value = String(value || "").trim();
    if (!value) return false;
    if (chosen(combo).indexOf(name + "\u0000" + value.toLowerCase()) >= 0) return false;
    var input = inputOf(combo);
    var tag = doc.createElement("span"); tag.className = "tag"; tag.setAttribute("data-chip", "");
    var hid = doc.createElement("input"); hid.type = "hidden"; hid.name = name; hid.value = value;
    var tt = doc.createElement("span"); tt.className = "tt"; tt.textContent = text || value;
    var x = doc.createElement("a"); x.className = "tx"; x.href = "#"; x.setAttribute("data-chip-remove", "");
    x.setAttribute("aria-label", "Remove " + (text || value)); x.textContent = "×";
    tag.appendChild(hid); tag.appendChild(tt); tag.appendChild(x);
    input.parentNode.insertBefore(tag, input);
    syncCombo(combo);
    return true;
  }
  function freeName(combo) {
    var f = combo.getAttribute("data-field");
    return FREE_ADD.test(f) ? f + "_add" : f;
  }
  function closeList() {
    if (!open) return;
    var list = listOf(open.combo), input = inputOf(open.combo);
    if (list) { list.hidden = true; list.innerHTML = ""; }
    if (input) { input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
    open = null;
  }
  function place(combo) {
    var list = listOf(combo), box = $(".sf-combo-box", combo), r = box.getBoundingClientRect();
    list.style.position = "fixed"; list.style.left = r.left + "px"; list.style.width = r.width + "px"; list.style.right = "auto";
    var below = window.innerHeight - r.bottom - 12, above = r.top - 12, h = Math.min(list.scrollHeight, 264);
    var up = below < Math.min(h, 160) && above > below;
    list.style.maxHeight = Math.max(96, Math.min(264, up ? above : below)) + "px";
    h = Math.min(list.scrollHeight, parseInt(list.style.maxHeight, 10));
    list.style.top = (up ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
  }
  function render(combo, items, text) {
    var list = listOf(combo), input = inputOf(combo); if (!list) return;
    var taken = chosen(combo);
    items = items.filter(function (it) { return taken.indexOf(it.name + "\u0000" + String(it.value).toLowerCase()) < 0; });
    var t = text.trim();
    if (t && !items.some(function (it) { return String(it.label).toLowerCase() === t.toLowerCase(); })) {
      items.push({ name: freeName(combo), value: t, label: t, free: true });
    }
    list.innerHTML = "";
    if (!items.length) {
      if (!combo.hasAttribute("data-suggest")) { closeList(); return; }
      var none = doc.createElement("div"); none.className = "sf-list-note";
      none.textContent = "Type to search.";
      list.appendChild(none);
    }
    items.forEach(function (it, i) {
      var o = doc.createElement("div");
      o.className = "sf-opt" + (it.free ? " free" : ""); o.setAttribute("role", "option");
      o.id = input.id + "-o" + i; o.setAttribute("data-i", String(i));
      var l = doc.createElement("span"); l.className = "sf-ol";
      if (it.free) { l.appendChild(doc.createTextNode("Add ")); var b = doc.createElement("b"); b.textContent = "“" + it.label + "”"; l.appendChild(b); }
      else l.textContent = it.label;
      o.appendChild(l);
      if (it.hint) { var h = doc.createElement("span"); h.className = "sf-oh"; h.textContent = it.hint; o.appendChild(h); }
      list.appendChild(o);
    });
    open = { combo: combo, items: items, active: items.length ? 0 : -1 };
    list.hidden = false; input.setAttribute("aria-expanded", "true");
    place(combo); highlight();
  }
  function highlight() {
    if (!open) return;
    var input = inputOf(open.combo);
    $$(".sf-opt", listOf(open.combo)).forEach(function (o, i) {
      var on = i === open.active; o.setAttribute("aria-selected", String(on));
      if (on) { input.setAttribute("aria-activedescendant", o.id); o.scrollIntoView({ block: "nearest" }); }
    });
  }
  function suggest(combo, now) {
    var input = inputOf(combo), text = input.value, url = combo.getAttribute("data-suggest");
    clearTimeout(timer);
    if (!url) { if (text.trim()) render(combo, [], text); else closeList(); return; }
    var mine = ++seq;
    timer = setTimeout(function () {
      fetch(url + "&q=" + encodeURIComponent(text.trim()), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : { items: [] }; })
        .then(function (d) { if (mine === seq && doc.activeElement === input) render(combo, d.items || [], input.value); })
        .catch(function () { if (mine === seq) render(combo, [], input.value); });
    }, now ? 0 : 180);
  }
  function pick(combo, it) {
    var input = inputOf(combo), form = input.form;
    var added = addChip(combo, it.name, it.value, it.free ? String(it.value) : it.label);
    input.value = ""; closeList();
    if (added) { lastCombo = input.id; ping(form); }
  }
  function addTyped(combo) {
    var input = inputOf(combo), text = input.value, any = false;
    text.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).forEach(function (part) {
      if (addChip(combo, freeName(combo), part.trim(), part.trim())) any = true;
    });
    input.value = ""; closeList();
    if (any) { lastCombo = input.id; ping(input.form); }
  }
  var lastCombo = null;

  /* typing in a combobox never reaches htmx or the live count: only picked values count */
  doc.addEventListener("input", function (e) {
    var t = e.target;
    if (!t.classList || !t.classList.contains("sf-combo-input")) return;
    e.stopImmediatePropagation();
    suggest(comboOf(t));
  }, true);
  doc.addEventListener("change", function (e) {
    var t = e.target;
    if (t.classList && t.classList.contains("sf-combo-input")) e.stopImmediatePropagation();
  }, true);

  doc.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t.matches && t.matches("[data-submit-on-enter]") && e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); fire(t, "change"); return;
    }
    if (t.matches && t.matches(".sf-drag")) { dragKey(e, t); return; }
    if (!t.classList || !t.classList.contains("sf-combo-input")) return;
    var combo = comboOf(t), isOpen = open && open.combo === combo;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!isOpen) { suggest(combo, true); return; }
      var n = open.items.length; if (!n) return;
      open.active = (open.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n; highlight();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (isOpen && open.active >= 0 && open.items[open.active]) pick(combo, open.items[open.active]);
      else if (t.value.trim()) addTyped(combo);
    } else if (e.key === "," && t.value.trim() && t.value.split('"').length % 2 === 1) {
      e.preventDefault(); addTyped(combo);
    } else if (e.key === "Escape") {
      if (isOpen) { e.preventDefault(); e.stopPropagation(); closeList(); }
    } else if (e.key === "Backspace" && !t.value) {
      var chips = $$("[data-chip]", combo), last = chips[chips.length - 1];
      if (last) { last.remove(); syncCombo(combo); lastCombo = t.id; ping(t.form); }
    } else if (e.key === "Tab") closeList();
  });

  doc.addEventListener("mousedown", function (e) {
    if (e.target.closest && e.target.closest(".sf-combo-list")) e.preventDefault(); /* keep focus */
  });

  doc.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : e.target.parentElement;
    if (!t || !t.closest) return;
    var el;
    if ((el = t.closest(".sf-opt"))) {
      var combo = comboOf(el);
      if (open && open.combo === combo) pick(combo, open.items[Number(el.getAttribute("data-i"))]);
      return;
    }
    if ((el = t.closest("[data-chip-remove]"))) {
      e.preventDefault();
      var c = comboOf(el), input = inputOf(c);
      el.closest("[data-chip]").remove(); syncCombo(c); closeList(); ping(input.form);
      return;
    }
    if ((el = t.closest("[data-combo-clear]"))) {
      var cc = comboOf(el);
      $$("[data-chip]", cc).forEach(function (x) { x.remove(); });
      syncCombo(cc); closeList(); ping(inputOf(cc).form);
      return;
    }
    if ((el = t.closest(".sf-combo-box"))) {
      var cb = comboOf(el), inp = inputOf(cb);
      if (doc.activeElement !== inp) inp.focus();
      if (cb.hasAttribute("data-suggest") && !(open && open.combo === cb)) suggest(cb, true);
      return;
    }
    if (open && !t.closest("[data-combo]")) closeList();
    if ((el = t.closest(".sf-drag"))) { e.preventDefault(); return; } /* never toggles the group */
    if (t.closest("[data-pin]") || t.closest('[data-open-dialog="allf-dialog"]')) setTimeout(orderPins, 0);
  });
  doc.addEventListener("scroll", function (e) {
    if (!open) return;
    if (e.target && e.target.nodeType === 1 && e.target.closest && e.target.closest(".sf-combo-list")) return;
    place(open.combo);
  }, true);
  window.addEventListener("resize", function () { if (open) place(open.combo); });

  /* ---------------------------------------------------------------- reveal boxes, size modes */
  doc.addEventListener("change", function (e) {
    var t = e.target; if (!t.matches) return;
    if (t.matches("[data-reveal]")) {
      var box = doc.getElementById(t.getAttribute("data-reveal")); if (!box) return;
      reveals[t.getAttribute("data-reveal")] = t.checked;
      box.hidden = !t.checked;
      if (!t.checked) {
        var chips = $$("[data-chip]", box);
        chips.forEach(function (c) { c.remove(); });
        $$("[data-combo]", box).forEach(syncCombo);
        if (chips.length) ping(t.closest("form") || $("#f"));
      }
    } else if (t.matches("[data-size-mode]")) {
      var root = t.closest("[data-sizes]"), mode = t.getAttribute("data-size-mode"), changed = false;
      $$("[data-size-pane]", root).forEach(function (pane) {
        var on = pane.getAttribute("data-size-pane") === mode;
        pane.hidden = !on;
        if (on) return;
        $$("input[type=checkbox]:checked", pane).forEach(function (b) { b.checked = false; changed = true; });
        $$("input[type=number]", pane).forEach(function (n) { if (n.value) { n.value = ""; changed = true; } });
      });
      if (changed) ping(t.closest("form") || root.closest("form") || $("#f"));
    } else if (t.matches("[data-type-select]")) {
      var v = t.value, dlg = $("#allf-dialog"); if (!dlg) return;
      $$("[data-fbox]", dlg).forEach(function (col) {
        var k = col.getAttribute("data-fbox");
        col.hidden = k !== "pinned" && v !== "all" && k !== v;
      });
    }
  });

  /* ---------------------------------------------------------------- open state across swaps */
  var opened = {}, reveals = {}, panelScroll = 0;
  doc.addEventListener("toggle", function (e) {
    var d = e.target;
    if (d.matches && d.matches("details[data-keep-open]")) opened[d.getAttribute("data-keep-open")] = d.open;
  }, true);
  doc.addEventListener("htmx:beforeSwap", function () {
    var p = $(".fpanel"); panelScroll = p ? p.scrollTop : 0;
    if (doc.activeElement && doc.activeElement.classList && doc.activeElement.classList.contains("sf-combo-input")) lastCombo = doc.activeElement.id;
    closeList();
  });
  function restore() {
    $$("details[data-keep-open]").forEach(function (d) {
      var k = d.getAttribute("data-keep-open");
      if (k in opened && d.open !== opened[k]) d.open = opened[k];
    });
    $$("[data-reveal]").forEach(function (b) {
      var k = b.getAttribute("data-reveal"), box = doc.getElementById(k);
      if (!(k in reveals) || !box) return;
      var has = $$("[data-chip]", box).length > 0, on = reveals[k] || has;
      b.checked = on; box.hidden = !on;
    });
    var p = $(".fpanel"); if (p && panelScroll) p.scrollTop = panelScroll;
    if (lastCombo) {
      var el = doc.getElementById(lastCombo); lastCombo = null;
      if (el && !el.closest("dialog:not([open])")) el.focus({ preventScroll: true });
    }
  }

  /* ---------------------------------------------------------------- pinned order + drag */
  function pinKey() { return "lc-pins-" + kind(); }
  function pinList() {
    var raw = lsGet(pinKey());
    if (raw) { try { var v = JSON.parse(raw); if (Array.isArray(v)) return v; } catch (e) { /* bad value */ } }
    var panel = $(".fpanel");
    return panel ? (panel.getAttribute("data-pin-defaults") || "").split(",").filter(Boolean) : [];
  }
  function orderPins() {
    var list = pinList(), box = $('[data-flist="pinned"]');
    if (box) {
      list.forEach(function (key) {
        var g = $('.fgroup[data-fgroup="' + key + '"]', box); if (g) box.appendChild(g);
      });
    }
    var holder = $(".fpanel .fgroups"); if (!holder) return;
    var groups = $$(":scope > .fgroup", holder);
    groups.forEach(function (g, i) { if (!g.hasAttribute("data-porder")) g.setAttribute("data-porder", String(i)); });
    function rank(g) {
      var key = g.getAttribute("data-fgroup"), i = key ? list.indexOf(key) : -1;
      if (g.classList.contains("locked")) return 2000 + Number(g.getAttribute("data-porder"));
      return i >= 0 ? i : 1000 + Number(g.getAttribute("data-porder"));
    }
    groups.sort(function (a, b) { return rank(a) - rank(b); }).forEach(function (g) { holder.appendChild(g); });
  }
  function savePins(box) {
    var keys = $$(".fgroup[data-fgroup]", box).map(function (g) { return g.getAttribute("data-fgroup"); });
    lsSet(pinKey(), JSON.stringify(keys));
    orderPins();
  }
  var drag = null;
  doc.addEventListener("pointerdown", function (e) {
    var h = e.target.closest && e.target.closest(".sf-drag");
    if (!h || !h.closest('[data-flist="pinned"]') || e.button !== 0) return;
    e.preventDefault();
    var row = h.closest(".fgroup");
    drag = { row: row, box: row.parentNode, id: e.pointerId };
    row.classList.add("sf-dragging"); doc.body.classList.add("sf-drag-on");
    try { h.setPointerCapture(e.pointerId); } catch (err) { /* old browser */ }
  });
  doc.addEventListener("pointermove", function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    var rows = $$(":scope > .fgroup[data-fgroup]", drag.box).filter(function (r) { return r !== drag.row; });
    var before = null;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i].getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) { before = rows[i]; break; }
    }
    if (before) { if (drag.row.nextSibling !== before) drag.box.insertBefore(drag.row, before); }
    else if (drag.box.lastElementChild !== drag.row) drag.box.appendChild(drag.row);
  });
  function endDrag() {
    if (!drag) return;
    drag.row.classList.remove("sf-dragging"); doc.body.classList.remove("sf-drag-on");
    savePins(drag.box); drag = null;
  }
  doc.addEventListener("pointerup", endDrag);
  doc.addEventListener("pointercancel", endDrag);
  function dragKey(e, h) {
    var row = h.closest(".fgroup"), box = row && row.parentNode;
    if (!box || !box.matches('[data-flist="pinned"]')) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); return; }
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    var sib = e.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
    while (sib && !sib.matches(".fgroup[data-fgroup]")) sib = e.key === "ArrowUp" ? sib.previousElementSibling : sib.nextElementSibling;
    if (!sib) return;
    box.insertBefore(row, e.key === "ArrowUp" ? sib : sib.nextElementSibling);
    savePins(box); h.focus();
  }

  /* Simple mode never sends the Advanced expression (and keeps shared URLs clean) */
  doc.body.addEventListener("htmx:configRequest", function (e) {
    var d = e.detail, el = d.elt, fd = d.formData;
    if (!fd || !el || (el.id !== "f" && el.id !== "allf") || String(d.verb).toLowerCase() !== "get") return;
    if (fd.get("tmode") !== "advanced") fd.delete("texpr");
    /* "all=1" only matters while nothing else is asked: a real filter makes it redundant */
    var quiet = ["all", "tab", "sort", "per", "similar", "tmode", "kwfield", "kwall"];
    var asked = Array.from(fd.keys()).some(function (k) {
      return quiet.indexOf(k) < 0 && fd.getAll(k).some(function (v) { return v !== ""; });
    });
    if (asked) fd.delete("all");
  });

  function init() { $$("[data-combo]").forEach(syncCombo); orderPins(); restore(); }
  doc.addEventListener("htmx:afterSettle", init);
  init();
})();
