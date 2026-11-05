/* The Earth Has Receipts — frontend (vanilla JS, no build step).
 * All API strings are inserted with textContent / attributes only; never innerHTML. */
(function () {
  "use strict";

  const POLL_MS = 1500;
  const TERMINAL = new Set(["completed", "failed", "interrupted"]);

  const state = {
    mode: "mining",
    cases: [],
    currentId: null,
    pollGen: 0,
    pollTimer: null,
    last: null,          // last raw GET /investigations/{id} response
    view: null,          // DOM refs for the current investigation view
  };

  // ---------------------------------------------------------------- helpers
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = String(v);
        else if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
        else if (k === "style") node.setAttribute("style", v);
        else if (k === "hidden") node.hidden = !!v;
        else node.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const $ = (id) => document.getElementById(id);
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

  function safeUrl(u) {
    if (typeof u !== "string" || !u) return null;
    if (/^https?:\/\//i.test(u) || u.startsWith("/")) return u;
    return null;
  }

  function humanize(s) {
    if (s === null || s === undefined) return "";
    const t = String(s).replace(/[_\-]+/g, " ").replace(/\s+/g, " ").trim()
      .replace(/\b(ndvi|ndwi|bsi|nbr|rgb|vlm|llm|aoi|stac|id)\b/gi, (w) => w.toUpperCase());
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function fmtNum(v, digits) {
    if (typeof v !== "number" || !isFinite(v)) return String(v);
    const d = digits !== undefined ? digits : (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
    return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: d });
  }

  function fmtDate(s) {
    if (!s) return "—";
    const d = new Date(s);
    if (isNaN(d)) return String(s);
    return d.toISOString().slice(0, 10);
  }
  function fmtDateTime(s) {
    if (!s) return "—";
    const d = new Date(s);
    if (isNaN(d)) return String(s);
    return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }
  function fmtTime(s) {
    if (!s) return "";
    const d = new Date(s);
    if (isNaN(d)) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function fmtValue(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") return fmtNum(v, 4);
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  // Parse every error shape the backend may produce.
  async function parseError(resp) {
    let body = null;
    let text = "";
    try { text = await resp.text(); body = JSON.parse(text); } catch (_) { /* not JSON */ }
    const out = { code: "HTTP_" + resp.status, message: resp.statusText || "Request failed", items: [] };
    if (body && body.error && typeof body.error === "object") {
      out.code = body.error.code || out.code;
      out.message = body.error.message || out.message;
    } else if (body && Array.isArray(body.detail)) {
      out.code = "VALIDATION_ERROR";
      out.message = "The request was rejected:";
      out.items = body.detail.map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return (loc ? loc + ": " : "") + (d.msg || JSON.stringify(d));
      });
    } else if (body && typeof body.detail === "string") {
      out.message = body.detail;
    } else if (text) {
      out.message = text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 300) || out.message;
    }
    return out;
  }

  async function api(path, opts) {
    let resp;
    try {
      resp = await fetch(path, Object.assign({ headers: { Accept: "application/json" } }, opts || {}));
    } catch (e) {
      const err = new Error("Network error: backend unreachable");
      err.info = { code: "NETWORK_ERROR", message: "Could not reach the backend (" + e.message + ")", items: [] };
      throw err;
    }
    if (!resp.ok) {
      const info = await parseError(resp);
      const err = new Error(info.message);
      err.info = info;
      err.status = resp.status;
      throw err;
    }
    return resp.json();
  }

  function showErrorBox(box, info) {
    clear(box);
    box.appendChild(el("span", { class: "code", text: info.code }));
    box.appendChild(el("span", { text: info.message }));
    if (info.items && info.items.length) box.appendChild(el("ul", null, info.items.map((t) => el("li", { text: t }))));
    box.hidden = false;
  }

  // ---------------------------------------------------------------- health
  async function loadHealth() {
    const pill = $("health");
    const txt = pill.querySelector(".health-text");
    try {
      const h = await api("/health");
      pill.classList.remove("ok", "degraded", "down");
      pill.classList.add(h.status === "ok" ? "ok" : "degraded");
      const model = h.model ? String(h.model) : "model";
      const modelState = h.model_available ? "ready" : "unavailable";
      const sat = h.satellite_provider ? String(h.satellite_provider) : "satellite";
      txt.textContent = model + " " + modelState + " · " + sat;
      pill.title = JSON.stringify(h, null, 2);
    } catch (e) {
      pill.classList.remove("ok", "degraded");
      pill.classList.add("down");
      txt.textContent = "Backend offline";
      pill.title = e.message;
    }
  }

  // ---------------------------------------------------------------- form
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".mode-card").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    fillCaseSelect();
  }

  function fillCaseSelect() {
    const sel = $("case-select");
    clear(sel);
    const cases = state.cases.filter((c) => c.mode === state.mode);
    if (!cases.length) {
      sel.appendChild(el("option", { value: "", text: state.cases.length ? "No demo cases for this mode" : "No demo cases available" }));
    } else {
      for (const c of cases) sel.appendChild(el("option", { value: c.id, text: c.name || c.id }));
    }
    sel.appendChild(el("option", { value: "", text: "— none (custom location) —" }));
    onCaseChange();
  }

  function onCaseChange() {
    const sel = $("case-select");
    const info = $("case-info");
    const c = state.cases.find((x) => x.id === sel.value);
    clear(info);
    if (!c) {
      info.hidden = true;
      if (!sel.value) $("custom-loc").open = true;
      return;
    }
    $("claim").value = c.default_claim || "";
    if (c.description) info.appendChild(el("div", { text: c.description }));
    info.appendChild(el("div", { class: "loc", text:
      fmtNum(c.latitude, 4) + ", " + fmtNum(c.longitude, 4) + " · " + fmtNum(c.width_km, 1) + " km · " +
      (c.before_date || "?") + " → " + (c.after_date || "?") }));
    info.hidden = false;
    // Prefill custom fields with the case so the user can tweak them.
    $("c-lat").value = c.latitude ?? "";
    $("c-lon").value = c.longitude ?? "";
    $("c-width").value = c.width_km ?? "";
    $("c-before").value = c.before_date || "";
    $("c-after").value = c.after_date || "";
  }

  async function loadCases() {
    try {
      const cases = await api("/demo-cases");
      state.cases = Array.isArray(cases) ? cases : [];
    } catch (e) {
      state.cases = [];
      showErrorBox($("form-error"), e.info || { code: "ERROR", message: e.message });
    }
    fillCaseSelect();
  }

  async function onSubmit(ev) {
    ev.preventDefault();
    const btn = $("investigate-btn");
    const errBox = $("form-error");
    errBox.hidden = true;

    const claim = $("claim").value.trim();
    const body = { mode: state.mode, claim: claim };
    const caseId = $("case-select").value;
    const customOpen = $("custom-loc").open;
    const caseObj = state.cases.find((x) => x.id === caseId);

    if (customOpen) {
      const custom = {
        latitude: parseFloat($("c-lat").value),
        longitude: parseFloat($("c-lon").value),
        width_km: parseFloat($("c-width").value),
        before_date: $("c-before").value,
        after_date: $("c-after").value,
      };
      const filled = isFinite(custom.latitude) && isFinite(custom.longitude) && isFinite(custom.width_km) && custom.before_date && custom.after_date;
      const unchanged = caseObj && custom.latitude === caseObj.latitude && custom.longitude === caseObj.longitude &&
        custom.width_km === caseObj.width_km && custom.before_date === caseObj.before_date && custom.after_date === caseObj.after_date;
      if (filled && !unchanged) {
        body.custom = custom;
      } else if (!filled && !caseId) {
        showErrorBox(errBox, { code: "INCOMPLETE", message: "Fill in latitude, longitude, width and both dates for a custom location.", items: [] });
        return;
      } else if (caseId) {
        body.case_id = caseId;
      }
    } else if (caseId) {
      body.case_id = caseId;
    } else {
      showErrorBox(errBox, { code: "INCOMPLETE", message: "Choose a demo case or open “Custom location”.", items: [] });
      return;
    }

    btn.disabled = true;
    btn.textContent = "Submitting…";
    try {
      const res = await api("/investigations", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (!res || !res.investigation_id) throw Object.assign(new Error("No investigation_id in response"), { info: { code: "BAD_RESPONSE", message: "Backend returned no investigation_id", items: [] } });
      openInvestigation(res.investigation_id);
      loadRecent();
    } catch (e) {
      showErrorBox(errBox, e.info || { code: "ERROR", message: e.message, items: [] });
    } finally {
      btn.disabled = false;
      btn.textContent = "Investigate";
    }
  }

  // ---------------------------------------------------------------- recent
  async function loadRecent() {
    const list = $("recent-list");
    let items;
    try {
      items = await api("/investigations");
    } catch (e) {
      clear(list).appendChild(el("li", { class: "muted", text: "Could not load: " + e.message }));
      return;
    }
    clear(list);
    if (!Array.isArray(items) || !items.length) {
      list.appendChild(el("li", { class: "muted", text: "No investigations yet." }));
      return;
    }
    for (const it of items.slice(0, 30)) {
      const v = it.verdict_status;
      const btn = el("button", {
        type: "button", class: "recent-item" + (it.investigation_id === state.currentId ? " current" : ""),
        title: it.claim || "", on: { click: () => openInvestigation(it.investigation_id) },
      },
        el("span", { class: "recent-claim", text: it.claim || it.investigation_id }),
        el("span", { class: "recent-meta" },
          el("span", { text: it.mode === "forest" ? "ForestCheck" : it.mode === "mining" ? "MineWatch" : (it.mode || "") }),
          el("span", { class: "chip s-" + (it.status || ""), text: it.status || "?" }),
          v ? el("span", { class: "chip v-" + v, text: v }) : null,
          el("span", { text: fmtTime(it.created_at) })));
      btn.dataset.id = it.investigation_id;
      list.appendChild(el("li", null, btn));
    }
  }

  function markRecentCurrent() {
    document.querySelectorAll(".recent-item").forEach((b) => b.classList.toggle("current", b.dataset.id === state.currentId));
  }

  // ---------------------------------------------------------------- polling
  function openInvestigation(id) {
    state.pollGen += 1;
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.currentId = id;
    state.last = null;
    state.view = null;
    $("empty-state").hidden = true;
    const root = clear($("investigation"));
    root.hidden = false;
    root.appendChild(el("div", { class: "note", text: "Loading investigation " + id + "…" }));
    markRecentCurrent();
    poll(state.pollGen, 0);
  }

  async function poll(gen, failures) {
    if (gen !== state.pollGen) return;
    let data;
    try {
      data = await api("/investigations/" + encodeURIComponent(state.currentId));
    } catch (e) {
      if (gen !== state.pollGen) return;
      if (e.status === 404 || failures >= 8) {
        const root = clear($("investigation"));
        state.view = null;
        root.appendChild(fatalBox("failed", e.info || { code: "ERROR", message: e.message }));
        return;
      }
      state.pollTimer = setTimeout(() => poll(gen, failures + 1), POLL_MS * 2);
      return;
    }
    if (gen !== state.pollGen) return;
    state.last = data;
    try { render(data); } catch (err) { console.error("render failed", err); }
    if (TERMINAL.has(data.status)) {
      loadRecent();
      return;
    }
    state.pollTimer = setTimeout(() => poll(gen, 0), POLL_MS);
  }

  // ---------------------------------------------------------------- render
  function fatalBox(status, err) {
    const interrupted = status === "interrupted";
    return el("div", { class: "fatal" + (interrupted ? " interrupted" : "") },
      el("h3", { text: interrupted ? "Investigation interrupted" : "Investigation failed" }),
      el("div", { class: "code", text: (err && err.code) || (interrupted ? "INTERRUPTED" : "UNKNOWN_ERROR") }),
      el("div", { class: "msg", text: (err && err.message) || "No error message was provided." }));
  }

  function buildSkeleton() {
    const root = clear($("investigation"));
    const v = {};
    v.header = el("div", { class: "panel" });
    v.fatal = el("div");
    v.cache = el("div");
    v.verdict = el("div");
    v.imagery = el("section", { class: "panel" });
    v.timelinePanel = el("section", { class: "panel" },
      el("div", { class: "section-title" }, el("span", { text: "Agent activity" }), v.stepCount = el("span", { class: "muted", text: "" })));
    v.timeline = el("ol", { class: "timeline" });
    v.timelineEmpty = el("div", { class: "muted", text: "Waiting for the agent to start…" });
    v.timelinePanel.append(v.timelineEmpty, v.timeline);
    v.results = el("div", { class: "inv" });
    v.stepNodes = new Map();
    v.imageryKey = undefined;
    v.resultsKey = undefined;
    root.appendChild(el("div", { class: "inv" },
      v.header, v.fatal, v.cache, v.verdict,
      el("div", { class: "two-col" }, v.imagery, v.timelinePanel),
      v.results));
    return v;
  }

  function render(d) {
    if (!state.view || state.view.id !== d.investigation_id) {
      state.view = buildSkeleton();
      state.view.id = d.investigation_id;
    }
    const v = state.view;
    renderHeader(v, d);

    clear(v.fatal);
    if (d.status === "failed" || d.status === "interrupted") v.fatal.appendChild(fatalBox(d.status, d.error));

    clear(v.cache);
    if (d.from_cache) v.cache.appendChild(el("div", { class: "note cache", text: "Replayed from cache — imagery acquired on the dates shown" }));

    renderTimeline(v, d);

    const imgKey = d.imagery ? JSON.stringify(d.imagery) : "none:" + TERMINAL.has(d.status);
    if (imgKey !== v.imageryKey) {
      v.imageryKey = imgKey;
      renderImagery(v, d);
    }

    const resKey = JSON.stringify([d.verdict, d.headline, d.measurements, d.visual_analysis, d.methodology, d.sources, d.limitations]);
    if (resKey !== v.resultsKey) {
      v.resultsKey = resKey;
      renderVerdict(v, d);
      renderResults(v, d);
    }
  }

  function renderHeader(v, d) {
    clear(v.header);
    const loc = d.location;
    const ts = d.temporal_scope;
    const status = d.status || "queued";
    const pct = Math.max(0, Math.min(100, Number(d.progress_percent) || (status === "completed" ? 100 : 0)));

    const dl = el("button", { type: "button", class: "btn-ghost", on: { click: downloadReport } }, "Download JSON report");
    v.header.appendChild(el("div", { class: "inv-header" },
      el("div", null,
        el("div", { class: "inv-claim", text: d.claim || "(no claim text)" }),
        el("div", { class: "inv-meta" },
          el("span", { text: d.mode === "forest" ? "ForestCheck" : d.mode === "mining" ? "MineWatch" : (d.mode || "") }),
          loc ? el("span", { text: (loc.name ? loc.name + " · " : "") + fmtNum(loc.latitude, 4) + ", " + fmtNum(loc.longitude, 4) + " · " + fmtNum(loc.width_km, 1) + " km" }) : null,
          ts ? el("span", { text: "Before ≈ " + (ts.before_target || "?") + " · After ≈ " + (ts.after_target || "?") }) : null,
          el("span", { class: "mono", text: d.investigation_id }))),
      el("div", { class: "inv-actions" }, el("span", { class: "chip s-" + status, text: status }), dl)));

    v.header.appendChild(el("div", { style: "margin-top:12px" },
      el("div", { class: "progress-row" },
        el("div", { class: "progress" }, el("div", { class: "progress-bar" + (status === "failed" ? " failed" : status === "interrupted" ? " interrupted" : ""), style: "width:" + pct + "%" })),
        el("span", { class: "progress-label", text: pct + "%" })),
      d.current_step && !TERMINAL.has(status) ? el("div", { class: "current-step", text: "Now: " + humanize(d.current_step) }) : null));
  }

  function downloadReport() {
    if (!state.last) return;
    const blob = new Blob([JSON.stringify(state.last, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: (state.last.investigation_id || "investigation") + ".json" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---------------------------------------------------------------- timeline
  function renderTimeline(v, d) {
    const steps = Array.isArray(d.steps) ? d.steps.slice().sort((a, b) => (a.index ?? 0) - (b.index ?? 0)) : [];
    v.timelineEmpty.hidden = steps.length > 0;
    v.timelineEmpty.textContent = TERMINAL.has(d.status) ? "No steps were recorded." : "Waiting for the agent to start…";
    v.stepCount.textContent = steps.length ? steps.length + " step" + (steps.length === 1 ? "" : "s") : "";
    const seen = new Set();
    steps.forEach((s, i) => {
      const key = s.index !== undefined ? String(s.index) : "i" + i;
      seen.add(key);
      let node = v.stepNodes.get(key);
      if (!node) {
        node = createStepNode();
        v.stepNodes.set(key, node);
      }
      updateStepNode(node, s);
      // keep DOM order equal to step order
      if (v.timeline.children[i] !== node.li) v.timeline.insertBefore(node.li, v.timeline.children[i] || null);
    });
    for (const [key, node] of v.stepNodes) {
      if (!seen.has(key)) { node.li.remove(); v.stepNodes.delete(key); }
    }
  }

  function createStepNode() {
    const n = {};
    n.icon = el("div", { class: "tl-icon" });
    n.action = el("span", { class: "tl-action" });
    n.badge = el("span", { class: "chip" });
    n.time = el("span", { class: "tl-time" });
    n.summary = el("div", { class: "tl-summary" });
    n.reasonText = el("span");
    n.reason = el("div", { class: "tl-reason" }, el("span", { class: "why", text: "why" }), n.reasonText);
    n.pre = el("pre", { class: "json" });
    n.details = el("details", { class: "tl-details" }, el("summary", { text: "details" }), n.pre);
    n.li = el("li", { class: "tl-item" }, n.icon,
      el("div", { class: "tl-head" }, n.action, n.badge, n.time), n.summary, n.reason, n.details);
    n.sig = null;
    return n;
  }

  function updateStepNode(n, s) {
    const sig = JSON.stringify(s);
    if (sig === n.sig) return;
    n.sig = sig;
    const status = s.status || "running";
    n.li.className = "tl-item " + status;
    clear(n.icon);
    if (status === "completed") n.icon.textContent = "✓";
    else if (status === "failed") n.icon.textContent = "✗";
    else n.icon.appendChild(el("div", { class: "spinner" }));
    n.icon.title = status;
    n.action.textContent = humanize(s.action || "step");
    const by = s.decided_by || "system";
    n.badge.className = "chip " + by;
    n.badge.textContent = by === "llm" ? "LLM decision" : by === "rule" ? "rule" : "system";
    n.badge.title = "decided by: " + by;
    const t0 = fmtTime(s.started_at);
    let dur = "";
    if (s.started_at && s.finished_at) {
      const ms = new Date(s.finished_at) - new Date(s.started_at);
      if (isFinite(ms) && ms >= 0) dur = " · " + (ms / 1000).toFixed(1) + "s";
    }
    n.time.textContent = t0 + dur;
    n.summary.textContent = s.summary || "";
    n.summary.hidden = !s.summary;
    n.reasonText.textContent = s.reason || "";
    n.reason.hidden = !s.reason;
    const hasDetails = s.details && typeof s.details === "object" && Object.keys(s.details).length > 0;
    n.details.hidden = !hasDetails;
    if (hasDetails) n.pre.textContent = JSON.stringify(s.details, null, 2);
  }

  // ---------------------------------------------------------------- imagery
  function renderImagery(v, d) {
    const box = clear(v.imagery);
    const img = d.imagery;
    const tabsBar = el("div", { class: "imagery-tabs" });
    box.appendChild(el("div", { class: "section-title" }, el("span", { text: "Satellite evidence" }), tabsBar));
    if (!img || !img.before || !img.after) {
      box.appendChild(el("div", { class: "img-missing", text: TERMINAL.has(d.status) ? "No imagery was produced for this investigation." : "Imagery will appear once the agent has retrieved it." }));
      return;
    }
    const stage = el("div");
    box.appendChild(stage);

    const views = [{ key: "compare", label: "Before / After" }];
    if (safeUrl(img.overlay_url)) views.push({ key: "overlay", label: "Change overlay", url: img.overlay_url });
    if (img.extra && typeof img.extra === "object") {
      for (const [label, url] of Object.entries(img.extra)) if (safeUrl(url)) views.push({ key: "x:" + label, label: humanize(label), url: url });
    }
    const crisp = { on: false };
    const compare = buildCompare(img, crisp);
    let current = "compare";
    const buttons = views.map((vw) => el("button", {
      type: "button", class: "btn-ghost small" + (vw.key === current ? " active" : ""),
      on: { click: () => show(vw.key) },
    }, vw.label));
    if (views.length > 1) buttons.forEach((b) => tabsBar.appendChild(b));

    function show(key) {
      current = key;
      buttons.forEach((b, i) => b.classList.toggle("active", views[i].key === key));
      clear(stage);
      if (key === "compare") { stage.appendChild(compare.root); return; }
      const vw = views.find((x) => x.key === key);
      const im = el("img", { src: safeUrl(vw.url), alt: vw.label, loading: "lazy" });
      im.addEventListener("error", () => im.replaceWith(el("div", { class: "img-missing", text: "Image could not be loaded: " + vw.url })));
      stage.appendChild(el("div", { class: "gallery-view" + (crisp.on ? " crisp" : "") }, im,
        el("div", { class: "gallery-caption", text: vw.label })));
    }
    show("compare");
  }

  function buildCompare(img, crisp) {
    const beforeDate = fmtDate(img.before.acquired_at || img.before.date);
    const afterDate = fmtDate(img.after.acquired_at || img.after.date);
    const imBefore = el("img", { class: "layer-before", src: safeUrl(img.before.url), alt: "Before, acquired " + beforeDate, draggable: "false" });
    const imAfter = el("img", { class: "layer-after", src: safeUrl(img.after.url), alt: "After, acquired " + afterDate, draggable: "false" });
    const maskUrl = safeUrl(img.mask_url);
    const imMask = maskUrl ? el("img", { class: "layer-mask", src: maskUrl, alt: "Change mask", draggable: "false", hidden: true }) : null;
    const knob = el("div", { class: "compare-knob", text: "⟷" });
    const handle = el("div", { class: "compare-handle", tabindex: "0", role: "slider", "aria-label": "Before/after divider",
      "aria-valuemin": "0", "aria-valuemax": "100" }, knob);
    const container = el("div", { class: "compare" }, imAfter, imBefore, imMask,
      el("div", { class: "compare-label left" }, el("b", { text: "BEFORE" }), beforeDate),
      el("div", { class: "compare-label right" }, el("b", { text: "AFTER" }), afterDate),
      handle);

    let pos = 50;
    function setPos(p) {
      pos = Math.max(0, Math.min(100, p));
      imBefore.style.clipPath = "inset(0 " + (100 - pos) + "% 0 0)";
      handle.style.left = pos + "%";
      handle.setAttribute("aria-valuenow", String(Math.round(pos)));
      handle.setAttribute("aria-valuetext", Math.round(pos) + "% before");
    }
    setPos(50);

    function fromEvent(e) {
      const r = container.getBoundingClientRect();
      if (r.width > 0) setPos(((e.clientX - r.left) / r.width) * 100);
    }
    let dragging = false;
    container.addEventListener("pointerdown", (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      dragging = true;
      try { container.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
      fromEvent(e);
      handle.focus({ preventScroll: true });
      e.preventDefault();
    });
    container.addEventListener("pointermove", (e) => { if (dragging) fromEvent(e); });
    const stop = (e) => { dragging = false; try { container.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ } };
    container.addEventListener("pointerup", stop);
    container.addEventListener("pointercancel", stop);
    handle.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 10 : 2;
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") setPos(pos - step);
      else if (e.key === "ArrowRight" || e.key === "ArrowUp") setPos(pos + step);
      else if (e.key === "Home") setPos(0);
      else if (e.key === "End") setPos(100);
      else return;
      e.preventDefault();
    });

    const controls = el("div", { class: "imagery-controls" });
    if (imMask) {
      const cb = el("input", { type: "checkbox" });
      cb.addEventListener("change", () => { imMask.hidden = !cb.checked; });
      controls.appendChild(el("label", null, cb, "Show change mask"));
    } else {
      controls.appendChild(el("span", { class: "muted", text: "No change mask available" }));
    }
    const crispCb = el("input", { type: "checkbox" });
    crispCb.addEventListener("change", () => { crisp.on = crispCb.checked; container.classList.toggle("crisp", crisp.on); });
    controls.appendChild(el("label", null, crispCb, "Pixel-crisp"));
    controls.appendChild(el("span", { class: "muted", text: "Drag the divider or use ←/→ keys" }));

    const sceneIds = el("div", { class: "scene-ids" },
      el("div", { text: "Before: " + (img.before.scene_id || "—") + " · " + fmtDateTime(img.before.acquired_at || img.before.date) }),
      el("div", { text: "After: " + (img.after.scene_id || "—") + " · " + fmtDateTime(img.after.acquired_at || img.after.date) }));

    return { root: el("div", null, container, controls, sceneIds) };
  }

  // ---------------------------------------------------------------- verdict & results
  function renderVerdict(v, d) {
    clear(v.verdict);
    const vd = d.verdict;
    if (!vd) return;
    const status = vd.status || "inconclusive";
    v.verdict.appendChild(el("div", { class: "verdict " + status },
      el("div", { class: "verdict-kicker", text: "Verdict" }),
      el("div", { class: "verdict-top" },
        el("span", { class: "verdict-label", text: status }),
        vd.confidence ? el("span", { class: "chip cert-" + vd.confidence, text: vd.confidence + " confidence" }) : null),
      vd.summary ? el("div", { class: "verdict-summary", text: vd.summary }) : null));
  }

  function list(items, emptyText) {
    if (!Array.isArray(items) || !items.length) return el("div", { class: "muted", text: emptyText || "None." });
    return el("ul", { class: "plain" }, items.map((t) => el("li", { text: typeof t === "string" ? t : JSON.stringify(t) })));
  }

  function panel(title, ...children) {
    return el("section", { class: "panel" }, el("div", { class: "section-title" }, el("span", { text: title })), ...children);
  }

  function isCardKey(k, val) {
    return typeof val === "number" && isFinite(val) && (/_ha$/.test(k) || /fraction/.test(k));
  }

  function measurementCard(k, val) {
    let txt, unit = "";
    if (/fraction/.test(k)) { txt = fmtNum(val * 100, 1); unit = "%"; }
    else { txt = fmtNum(val); unit = "ha"; }
    const label = humanize(k.replace(/_ha$/, "").replace(/_fraction$/, ""));
    return el("div", { class: "card", title: k }, el("div", { class: "k", text: label }), el("div", { class: "v" }, txt, el("span", { class: "u", text: unit })));
  }

  function kvTable(obj, prefix, rows) {
    rows = rows || [];
    for (const [k, val] of Object.entries(obj || {})) {
      const key = prefix ? prefix + "." + k : k;
      if (val && typeof val === "object" && !Array.isArray(val)) kvTable(val, key, rows);
      else rows.push(el("tr", null, el("td", { text: key }), el("td", { text: fmtValue(val) })));
    }
    return rows;
  }

  function renderResults(v, d) {
    const R = clear(v.results);
    const vd = d.verdict;
    const m = d.measurements && typeof d.measurements === "object" ? d.measurements : null;

    // headline + key measurements
    const cards = el("div", { class: "cards" });
    if (d.headline) {
      const hv = d.headline.value;
      cards.appendChild(el("div", { class: "card headline" },
        el("div", { class: "k", text: d.headline.label || "Headline" }),
        el("div", { class: "v" }, typeof hv === "number" ? fmtNum(hv) : fmtValue(hv), el("span", { class: "u", text: d.headline.unit || "" }))));
    }
    const rest = {};
    if (m) {
      for (const [k, val] of Object.entries(m)) {
        if (k === "regions" || k === "thresholds") continue;
        if (isCardKey(k, val)) cards.appendChild(measurementCard(k, val));
        else rest[k] = val;
      }
    }
    if (cards.children.length || Object.keys(rest).length || (m && m.thresholds)) {
      const p = panel("Measurements", cards);
      if (!cards.children.length) cards.appendChild(el("div", { class: "muted", text: "No area measurements." }));
      const restRows = kvTable(rest);
      if (m && m.thresholds) kvTable(m.thresholds, "thresholds", restRows);
      if (restRows.length) {
        p.appendChild(el("details", { class: "collapsible", style: "margin-top:10px" },
          el("summary", { text: "All measurements (" + restRows.length + ")" }), el("table", { class: "kv" }, el("tbody", null, restRows))));
      }
      R.appendChild(p);
    }

    // observed / inferred / not established + rationale
    if (vd) {
      R.appendChild(panel("Evidence breakdown",
        el("div", { class: "triad" },
          el("div", { class: "triad-col observed" }, el("h4", { text: "Observed" }), list(vd.observed)),
          el("div", { class: "triad-col inferred" }, el("h4", { text: "Inferred" }), list(vd.inferred)),
          el("div", { class: "triad-col notest" }, el("h4", { text: "Not established" }), list(vd.not_established))),
        el("div", { class: "subhead", text: "Rationale" }), list(vd.rationale)));
    }

    // visual analysis + regions
    const row = el("div", { class: "grid-2" });
    if (d.visual_analysis) row.appendChild(renderVisual(d.visual_analysis));
    const regions = m && Array.isArray(m.regions) ? m.regions : null;
    if (regions && regions.length) row.appendChild(renderRegions(regions));
    if (row.children.length) R.appendChild(row);

    // methodology + sources
    const row2 = el("div", { class: "grid-2" });
    if (d.methodology && typeof d.methodology === "object" && Object.keys(d.methodology).length) {
      row2.appendChild(panel("Methodology", el("table", { class: "kv" }, el("tbody", null, kvTable(d.methodology)))));
    }
    if (Array.isArray(d.sources) && d.sources.length) {
      row2.appendChild(panel("Sources", el("ul", { class: "sources" }, d.sources.map((s) => {
        const u = safeUrl(s.url);
        return el("li", null,
          el("div", { class: "sid", text: s.scene_id || "(no scene id)" }),
          el("div", { class: "smeta", text: [s.type, s.provider, s.collection].filter(Boolean).join(" · ") + " · acquired " + fmtDateTime(s.acquired_at) }),
          u ? el("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: "Open source record ↗" }) : null);
      }))));
    }
    if (row2.children.length) R.appendChild(row2);

    if (Array.isArray(d.limitations) && d.limitations.length) R.appendChild(panel("Limitations", list(d.limitations)));
  }

  function renderVisual(va) {
    const p = panel("Visual interpretation" + (va.model ? " · " + va.model : ""));
    const a = va.analysis;
    if (va.status !== "ok" || !a) {
      p.appendChild(el("div", { class: "note", text: "Visual interpretation unavailable" + (va.error ? ": " + va.error : ".") }));
      return p;
    }
    if (a.summary) p.appendChild(el("div", { text: a.summary, style: "margin-bottom:8px" }));
    if (typeof a.changes_consistent_with_measurements !== "undefined" && a.changes_consistent_with_measurements !== null) {
      const c = a.changes_consistent_with_measurements;
      p.appendChild(el("div", { class: "note", style: "margin-bottom:8px", text: "Consistent with measurements: " + (typeof c === "boolean" ? (c ? "yes" : "no") : String(c)) }));
    }
    const obs = Array.isArray(a.observations) ? a.observations : [];
    if (obs.length) {
      p.appendChild(el("div", { class: "obs" }, obs.map((o) => el("div", { class: "obs-item" },
        el("div", { class: "obs-head" }, el("span", { class: "obs-feature", text: o.feature || "Observation" }),
          o.certainty ? el("span", { class: "chip cert-" + o.certainty, text: o.certainty }) : null),
        el("div", { class: "obs-text", text: o.observation || "" }),
        o.possible_explanation ? el("div", { class: "obs-expl", text: "Possible explanation: " + o.possible_explanation }) : null))));
    }
    if (Array.isArray(a.alternative_explanations) && a.alternative_explanations.length) {
      p.append(el("div", { class: "subhead", text: "Alternative explanations" }), list(a.alternative_explanations));
    }
    if (Array.isArray(a.limitations) && a.limitations.length) {
      p.append(el("div", { class: "subhead", text: "Visual limitations" }), list(a.limitations));
    }
    if (Array.isArray(a.additional_evidence_needed) && a.additional_evidence_needed.length) {
      p.append(el("div", { class: "subhead", text: "Additional evidence needed" }), list(a.additional_evidence_needed));
    }
    return p;
  }

  function renderRegions(regions) {
    const skip = new Set(["id", "area_ha", "centroid_lonlat"]);
    const extraKeys = [];
    for (const r of regions) for (const k of Object.keys(r || {})) if (!skip.has(k) && !extraKeys.includes(k)) extraKeys.push(k);
    const head = el("tr", null, el("th", { text: "ID" }), el("th", { class: "num", text: "Area (ha)" }), el("th", { class: "num", text: "Lat" }), el("th", { class: "num", text: "Lon" }),
      extraKeys.map((k) => el("th", { text: humanize(k) })));
    const rows = regions.map((r) => {
      const c = Array.isArray(r.centroid_lonlat) ? r.centroid_lonlat : [];
      return el("tr", null,
        el("td", { class: "mono", text: r.id ?? "" }),
        el("td", { class: "num", text: typeof r.area_ha === "number" ? fmtNum(r.area_ha, 1) : fmtValue(r.area_ha) }),
        el("td", { class: "num", text: typeof c[1] === "number" ? c[1].toFixed(4) : "—" }),
        el("td", { class: "num", text: typeof c[0] === "number" ? c[0].toFixed(4) : "—" }),
        extraKeys.map((k) => {
          const val = r[k];
          return el("td", { text: Array.isArray(val) ? val.map(fmtValue).join(", ") : fmtValue(val) });
        }));
    });
    return panel("Change regions (" + regions.length + ")",
      el("div", { style: "overflow-x:auto" }, el("table", { class: "data" }, el("thead", null, head), el("tbody", null, rows))));
  }

  // ---------------------------------------------------------------- init
  function init() {
    document.querySelectorAll(".mode-card").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    $("case-select").addEventListener("change", onCaseChange);
    $("inv-form").addEventListener("submit", onSubmit);
    $("refresh-recent").addEventListener("click", loadRecent);
    loadHealth();
    setInterval(loadHealth, 30000);
    loadCases();
    loadRecent();
    const m = /^#inv=(.+)$/.exec(location.hash);
    if (m) openInvestigation(decodeURIComponent(m[1]));
  }

  document.addEventListener("DOMContentLoaded", init);
})();
