"use strict";
// Labler VC-500W web UI. Server-renders-with-client-overlay editor: the canvas is
// the real Pillow PNG from /api/render; element manipulation is an HTML overlay that
// POSTs the display-list back on every change.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// ---- editor debug logging ---------------------------------------------------
// Compact console trace of element positions through the add/move/save/load
// lifecycle. Off by default; enable with localStorage.setItem('labler_debug','1').
// Each line shows [x,y w×h "text"] per element so you can see what the design holds.
const DEBUG = localStorage.getItem("labler_debug") === "1";
function elDigest(els) {
  return (els || []).map(e => e.type === "border"
    ? `border` : `${e.type}[${e.x},${e.y} ${e.w}×${e.h ?? "-"} z${e.z} "${(e.text ?? "").slice(0,8)}"]`
  ).join("  ");
}
function dlog(tag, ...rest) {
  if (!DEBUG) return;
  console.log(`%c[labler] ${tag}`, "color:#4f8cff;font-weight:600", ...rest);
}
const api = {
  async json(url, opts) { const r = await fetch(url, opts); return r.json(); },
  async post(url, body) {
    return this.json(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  },
  async del(url) { return this.json(url, { method: "DELETE" }); },
};

// ---- editor state -----------------------------------------------------------
let design = newDesign();
let selected = null;     // index into design.elements
let renderTimer = null;
let settingsCache = {};

function newDesign() {
  return { name: "", media_mm: 25, length_px: "auto", rotate: 0, background: "white", elements: [] };
}

// ---- tabs -------------------------------------------------------------------
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
  if (t.dataset.tab === "device") loadDevice();
  if (t.dataset.tab === "history") loadHistory();
  if (t.dataset.tab === "about") loadAbout();
  if (t.dataset.tab === "print") renderPrintPreview();
}));

// ---- boot -------------------------------------------------------------------
(async function boot() {
  const ping = await api.json("/api/ping");
  $("#version").textContent = "v" + ping.version;
  if (DEBUG) console.log("%c[labler] editor debug logging ON — silence with localStorage.removeItem('labler_debug')", "color:#3ecf8e");
  await loadSettings();
  await loadFonts();
  pollStatus();
  setInterval(pollStatus, 15000);
  addElement("text");           // start with one text element so the canvas isn't empty
})();

// ---- status pill ------------------------------------------------------------
async function pollStatus() {
  const pill = $("#status-pill");
  try {
    const s = await api.json("/api/status");
    if (s.ok) {
      pill.textContent = s.ready ? "ready" : (s.state || "?");
      pill.className = "pill " + (s.ready ? "ok" : "bad");
      pill.title = `state=${s.state} stage=${s.stage} remain=${s.remain_in ?? "?"}"`;
    } else { pill.textContent = "offline"; pill.className = "pill bad"; pill.title = s.error || ""; }
  } catch (e) { pill.textContent = "offline"; pill.className = "pill bad"; }
}

// ============================ EDITOR ========================================
function addElement(type) {
  const w = design.media_mm === 50 ? 624 : 312;
  const z = design.elements.length;
  // Start new elements just below whatever's already there, so multiple adds
  // don't pile up at the same y (which made the canvas not match the list).
  const y0 = nextFreeY();
  let el;
  if (type === "image") el = { type, x: 0, y: y0, w, h: 200, rotate: 0, z, src_id: null, fit: "contain" };
  else if (type === "text") el = { type, x: 8, y: y0, w: w - 16, h: 80, rotate: 0, z, text: "Label", font: settingsCache.font || defaultFont() || null, font_size: 56, color: "black", align: "center", bold: false, italic: false };
  else if (type === "border") el = { type, z: 99, color: "black", thickness: 4 };
  design.elements.push(el);
  selected = design.elements.length - 1;
  dlog(`addElement ${type} @y=${y0}`, elDigest(design.elements));
  if (type === "image") pickImageFor(el);
  renderEditor();
}

// Lowest free y on the canvas: bottom edge of the lowest positioned element + a
// small gap (borders have no y/h, so they're skipped). Keeps stacked adds visible.
function nextFreeY() {
  let bottom = 8;
  for (const el of design.elements) {
    if (el.type === "border") continue;
    const b = (el.y || 0) + (el.h || 40);
    if (b > bottom) bottom = b;
  }
  return bottom + 8;
}

$$("[data-add]").forEach(b => b.addEventListener("click", () => addElement(b.dataset.add)));

function pickImageFor(el) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = "image/*";
  inp.onchange = async () => {
    const fd = new FormData(); fd.append("file", inp.files[0]);
    const r = await fetch("/api/assets", { method: "POST", body: fd }).then(x => x.json());
    if (r.ok) {
      el.src_id = r.id;
      // fit the imported image to tape width preserving aspect
      el.h = Math.round(r.h * (el.w / r.w));
      renderEditor();
    } else alert("upload failed: " + r.error);
  };
  inp.click();
}

function renderEditor() {
  renderElementList();
  renderProps();
  scheduleRender();
}

function renderElementList() {
  const ul = $("#element-list"); ul.innerHTML = "";
  design.elements.forEach((el, i) => {
    const li = document.createElement("li");
    if (i === selected) li.classList.add("sel");
    const label = el.type === "text" ? `text: ${el.text.slice(0, 12)}` : el.type;
    li.innerHTML = `<span class="el-type">${label}</span>
      <button class="el-btn" data-up="${i}">↑</button>
      <button class="el-btn" data-down="${i}">↓</button>
      <button class="el-btn" data-del="${i}">✕</button>`;
    li.querySelector(".el-type").onclick = () => { selected = i; renderEditor(); };
    ul.appendChild(li);
  });
  ul.querySelectorAll("[data-del]").forEach(b => b.onclick = e => { e.stopPropagation(); design.elements.splice(+b.dataset.del, 1); selected = null; renderEditor(); });
  ul.querySelectorAll("[data-up]").forEach(b => b.onclick = e => { e.stopPropagation(); moveZ(+b.dataset.up, -1); });
  ul.querySelectorAll("[data-down]").forEach(b => b.onclick = e => { e.stopPropagation(); moveZ(+b.dataset.down, +1); });
}

function moveZ(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= design.elements.length) return;
  [design.elements[i], design.elements[j]] = [design.elements[j], design.elements[i]];
  design.elements.forEach((el, k) => { if (el.type !== "border") el.z = k; });
  selected = j; renderEditor();
}

function renderProps() {
  const p = $("#props");
  if (selected == null || !design.elements[selected]) { p.innerHTML = `<p class="muted">Select or add an element.</p>`; return; }
  const el = design.elements[selected];
  let h = `<h3 style="margin-top:0">${el.type}</h3><div class="form">`;
  const num = (k, lbl, min = 0) => `<label>${lbl}<input type="number" data-k="${k}" value="${el[k] ?? 0}" min="${min}"></label>`;
  if (el.type === "text") {
    h += `<label>Text<input data-k="text" value="${escapeAttr(el.text)}"></label>`;
    h += `<label>Font<select data-k="font" id="prop-font"></select></label>`;
    const bOn = fontHas(el.font, "bold"), iOn = fontHas(el.font, "italic");
    h += `<div class="style-toggles" id="prop-style">
      <button type="button" class="style-btn${el.bold ? " on" : ""}" data-flag="bold"
        ${bOn ? "" : "disabled title='This font has no bold face'"} style="font-weight:bold">B</button>
      <button type="button" class="style-btn${el.italic ? " on" : ""}" data-flag="italic"
        ${iOn ? "" : "disabled title='This font has no italic face'"} style="font-style:italic">I</button>
    </div>`;
    h += num("font_size", "Size", 4);
    h += colorField("color", "Color", el.color);
    h += `<label>Align<select data-k="align"><option ${el.align==="left"?"selected":""}>left</option><option ${el.align==="center"?"selected":""}>center</option><option ${el.align==="right"?"selected":""}>right</option></select></label>`;
    h += num("x", "X") + num("y", "Y") + num("w", "Width", 1) + num("rotate", "Rotate");
  } else if (el.type === "image") {
    h += `<button id="prop-pick">Replace image…</button>`;
    h += `<label>Fit<select data-k="fit"><option ${el.fit==="contain"?"selected":""}>contain</option><option ${el.fit==="stretch"?"selected":""}>stretch</option></select></label>`;
    h += num("x", "X") + num("y", "Y") + num("w", "Width", 1) + num("h", "Height", 1) + num("rotate", "Rotate");
  } else if (el.type === "border") {
    h += colorField("color", "Color", el.color);
    h += num("thickness", "Thickness", 1);
  }
  h += `</div>`;
  p.innerHTML = h;
  p.querySelectorAll("[data-k]").forEach(inp => inp.oninput = () => {
    let v = inp.value;
    if (inp.type === "number") v = +v;
    el[inp.dataset.k] = v;
    if (inp.dataset.k === "color") syncSwatches(inp.dataset.k, v);
    if (inp.dataset.k === "text") renderElementList();
    // Changing the font can change which styles exist -> rebuild props so the
    // Bold/Italic toggles enable/disable correctly. Clear flags the new font lacks.
    if (inp.dataset.k === "font") {
      if (el.bold && !fontHas(v, "bold")) el.bold = false;
      if (el.italic && !fontHas(v, "italic")) el.italic = false;
      renderProps();
    }
    scheduleRender();
  });
  // Bold / Italic toggle buttons.
  p.querySelectorAll("[data-flag]").forEach(btn => btn.onclick = () => {
    if (btn.disabled) return;
    const flag = btn.dataset.flag;
    el[flag] = !el[flag];
    btn.classList.toggle("on", el[flag]);
    scheduleRender();
  });
  wireSwatches(p, el);
  if (el.type === "image") $("#prop-pick").onclick = () => pickImageFor(el);
  if (el.type === "text") fillFontSelect($("#prop-font"), el.font);
}

// Standard palette for the preset swatches. Order roughly mirrors the printer's
// vivid gamut (black/white + the 6 saturated primaries/secondaries) plus a few
// useful greys. Customizable presets are appended from settings (custom_colors).
const PALETTE = [
  "#000000", "#ffffff", "#ff0000", "#00a000", "#0000ff",
  "#ffff00", "#ff00ff", "#00bcd4", "#ff9800", "#795548",
  "#9e9e9e", "#607d8b",
];

// A color <input type=color> plus a row of preset swatches that set it on click.
function colorField(key, label, current) {
  const cur = toHex(current);
  const customs = (settingsCache.custom_colors || []);
  const all = [...PALETTE, ...customs.filter(c => !PALETTE.includes(c.toLowerCase()))];
  const sw = all.map(c =>
    `<button type="button" class="swatch" data-swatch="${key}" data-color="${c}"
       title="${c}" style="background:${c}"></button>`).join("");
  return `<label>${label}
    <input type="color" data-k="${key}" value="${cur}">
    <span class="swatches" data-swatches="${key}">${sw}</span>
  </label>`;
}

// Clicking a swatch writes its color into the matching <input type=color> and the
// element, then re-renders. Highlights the active swatch.
function wireSwatches(root, el) {
  const keys = new Set();
  root.querySelectorAll("[data-swatch]").forEach(b => {
    keys.add(b.dataset.swatch);
    b.onclick = () => {
      const key = b.dataset.swatch, color = b.dataset.color;
      const inp = root.querySelector(`input[data-k="${key}"]`);
      if (inp) inp.value = toHex(color);
      el[key] = color;
      syncSwatches(key, color);
      scheduleRender();
    };
  });
  // mark whichever swatch matches the current value — only for the color keys that
  // actually have a swatch row (NOT every el property: el.font_size etc. are numbers
  // and toHex() would crash on them, aborting the whole render -> broken-image bug).
  keys.forEach(k => syncSwatches(k, el[k]));
}
function syncSwatches(key, color) {
  const hex = toHex(color);
  $$(`[data-swatches="${key}"] .swatch`).forEach(b =>
    b.classList.toggle("active", toHex(b.dataset.color) === hex));
}

function scheduleRender() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(renderCanvas, 180);
}

async function renderCanvas() {
  syncMediaLength();
  // The edit canvas shows the design UNROTATED so the drag overlay's element
  // coordinates stay valid while you compose. The "Print preview" below shows the
  // REAL rotated render — exactly what feeds out of the printer.
  const editDL = { ...design, rotate: 0 };
  dlog(`renderCanvas — sending ${editDL.elements.length} els, length_px=${editDL.length_px}:`, elDigest(editDL.elements));
  const resp = await fetch("/api/render", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(editDL),
  });
  const blob = resp.ok ? await resp.blob() : null;
  if (resp.ok) dlog(`renderCanvas — got PNG ${resp.headers.get("X-Label-Width-Px")}×${resp.headers.get("X-Label-Length-Px")} px`);
  else dlog(`renderCanvas — /api/render FAILED ${resp.status}`);
  const img = $("#edit-preview");
  if (blob) img.src = URL.createObjectURL(blob);
  drawOverlay();
  renderPrintRender("#edit-tape-img", "#edit-ruler", "#edit-tape-used", design);
}

// Show the EXACT print render (same image the printer gets, just PNG). Orientation
// matches the printer: 25mm tape width = image width (the short way), length = image
// height (the long way, running top→bottom). A vertical cm ruler marks the length so
// you can read how much tape it uses. No view rotation — preview == print.
async function renderPrintRender(imgSel, rulerSel, usedSel, dl) {
  const resp = await fetch("/api/render", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dl),
  });
  if (!resp.ok) return;
  const wpx = +resp.headers.get("X-Label-Width-Px");   // across-tape (image width)
  const lpx = +resp.headers.get("X-Label-Length-Px");  // tape length (image height)
  const cm = +resp.headers.get("X-Label-Length-Cm");
  const inch = +resp.headers.get("X-Label-Length-In");
  const blob = await resp.blob();
  const img = $(imgSel);
  if (!img) return;
  // Scale so the 25mm width shows at a fixed on-screen width; length follows aspect.
  const TAPE_W = 90;                       // on-screen px for the 25mm tape width (50% bigger)
  const onscreenLen = lpx * (TAPE_W / wpx);
  img.style.width = TAPE_W + "px";
  img.style.height = onscreenLen + "px";
  img.src = URL.createObjectURL(blob);
  drawRuler(rulerSel, cm, onscreenLen);    // vertical ruler down the length
  const u = $(usedSel);
  if (u) u.textContent = `Tape used: ${cm} cm (${inch}″)`;
}

// Vertical cm ruler: ticks down the length (height) of the print preview.
function drawRuler(sel, totalCm, heightPx) {
  const ruler = $(sel); if (!ruler) return;
  ruler.innerHTML = ""; ruler.style.height = heightPx + "px";
  if (!totalCm || !heightPx) return;
  const pxPerCm = heightPx / totalCm;
  for (let c = 0; c <= Math.floor(totalCm); c++) {
    const t = document.createElement("div");
    t.className = "tick"; t.style.top = (c * pxPerCm) + "px";
    t.innerHTML = `<span>${c}</span>`;
    ruler.appendChild(t);
  }
}

function syncMediaLength() {
  design.media_mm = +$("#edit-media").value;
  const lv = $("#edit-length").value;
  design.length_px = lv === "auto" ? "auto" : +lv;
}

// overlay: a clickable hit-box per element so you can select by clicking on the
// canvas (not just via the list), plus a drag/resize selection box for the
// currently selected element. Mapped from label px -> screen px.
function drawOverlay() {
  const ov = $("#overlay"); ov.innerHTML = "";
  const img = $("#edit-preview");
  if (!img.naturalWidth) return;
  const scaleX = img.clientWidth / img.naturalWidth;
  const scaleY = img.clientHeight / img.naturalHeight;

  // Top-most element wins a click on overlapping regions, so iterate by z (the
  // last-drawn / highest-z element is checked first). We add hit-boxes in reverse
  // z-order but rely on later DOM nodes being on top, so add lowest first.
  design.elements.forEach((el, i) => {
    if (el.type === "border" || i === selected) return;  // selected gets the full box below
    const hit = document.createElement("div");
    hit.className = "hit-box";
    hit.style.left = (el.x * scaleX) + "px";
    hit.style.top = (el.y * scaleY) + "px";
    hit.style.width = (el.w * scaleX) + "px";
    hit.style.height = ((el.h || 40) * scaleY) + "px";
    hit.title = "click to select";
    hit.addEventListener("pointerdown", e => {
      e.preventDefault(); e.stopPropagation();
      selected = i; renderEditor();
    });
    ov.appendChild(hit);
  });

  const el = (selected != null) ? design.elements[selected] : null;
  if (!el || el.type === "border") return;
  const box = document.createElement("div");
  box.className = "sel-box";
  box.style.left = (el.x * scaleX) + "px";
  box.style.top = (el.y * scaleY) + "px";
  box.style.width = (el.w * scaleX) + "px";
  box.style.height = ((el.h || 40) * scaleY) + "px";
  box.innerHTML = `<div class="handle"></div>`;
  ov.appendChild(box);
  enableDrag(box, el, scaleX, scaleY);
}

function enableDrag(box, el, sx, sy) {
  const onDown = (e, mode) => {
    e.preventDefault();
    const start = pointer(e);
    const ox = el.x, oy = el.y, ow = el.w, oh = el.h || 40;
    const move = ev => {
      const p = pointer(ev);
      const dx = (p.x - start.x) / sx, dy = (p.y - start.y) / sy;
      if (mode === "move") { el.x = Math.round(ox + dx); el.y = Math.round(oy + dy); }
      else { el.w = Math.max(8, Math.round(ow + dx)); if ("h" in el) el.h = Math.max(8, Math.round(oh + dy)); }
      drawOverlay(); renderProps2Sync();
    };
    const up = () => {
      document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
      dlog(`drag end (${mode}) el#${selected} → x=${el.x},y=${el.y} w=${el.w} h=${el.h ?? "-"}`, elDigest(design.elements));
      scheduleRender();
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  };
  box.addEventListener("pointerdown", e => { if (!e.target.classList.contains("handle")) onDown(e, "move"); });
  box.querySelector(".handle").addEventListener("pointerdown", e => { e.stopPropagation(); onDown(e, "resize"); });
}
const pointer = e => ({ x: e.clientX, y: e.clientY });
function renderProps2Sync() { // update number inputs live during drag without full rebuild
  if (selected == null) return; const el = design.elements[selected];
  ["x", "y", "w", "h"].forEach(k => { const i = $(`[data-k="${k}"]`); if (i && k in el) i.value = el[k]; });
}

$("#edit-media").addEventListener("change", () => { syncMediaLength(); renderCanvas(); });
$("#edit-length").addEventListener("change", () => { syncMediaLength(); renderCanvas(); });

// Rotate the WHOLE label 90deg each click (0->90->180->270->0). Lets you flip a long
// design so it lies across the tape and uses far less length.
$("#btn-rotate-label").onclick = () => {
  design.rotate = ((design.rotate || 0) + 90) % 360;
  syncRotateLabel();
  renderCanvas();
};
function syncRotateLabel() { $("#rotate-deg").textContent = (design.rotate || 0) + "°"; }

// ---- design save / load -----------------------------------------------------
$("#btn-save-design").onclick = async () => {
  let name = $("#design-name").value.trim();
  if (!name) {
    name = (prompt("Name this design:", design.name || "") || "").trim();
    if (!name) { flash("#print-status", "save cancelled — a name is required"); return; }
    $("#design-name").value = name;
  }
  design.name = name;
  dlog(`SAVE "${name}" — POSTing elements:`, elDigest(design.elements));
  const r = await api.post("/api/designs", design);
  dlog(`SAVE done id=${r.id}`, r);
  if (r.ok) { design.id = r.id; flash("#print-status", "saved design: " + r.id); }
};
$("#btn-new-design").onclick = () => {
  const name = (prompt("Name for the new design:", "") || "").trim();
  if (!name) return;  // cancelled — keep current design
  design = newDesign();
  design.name = name;
  selected = null;
  $("#design-name").value = name;
  syncRotateLabel();
  addElement("text");
};
$("#btn-load-design").onclick = async () => {
  const { designs } = await api.json("/api/designs");
  const ul = $("#design-list"); ul.innerHTML = "";
  designs.forEach(d => {
    const li = document.createElement("li");
    li.innerHTML = `${d.preview ? `<img src="${d.preview}">` : ""}<span class="d-name">${d.name}</span><button data-del="${d.id}">✕</button>`;
    li.querySelector(".d-name").onclick = async () => {
      design = await api.json("/api/designs/" + d.id);
      dlog(`LOAD "${d.id}" — server returned elements:`, elDigest(design.elements));
      if (!design.elements) design = newDesign();
      migrateFonts(design);
      if (design.rotate == null) design.rotate = 0;
      $("#design-name").value = design.name || "";
      $("#edit-media").value = design.media_mm; $("#edit-length").value = design.length_px;
      selected = design.elements.length ? 0 : null;
      syncRotateLabel();
      $("#modal").classList.add("hidden"); renderEditor();
      dlog(`LOAD applied — design.elements now:`, elDigest(design.elements));
    };
    li.querySelector("[data-del]").onclick = async () => { await api.del("/api/designs/" + d.id); li.remove(); };
    ul.appendChild(li);
  });
  $("#modal").classList.remove("hidden");
};
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");

// ============================ PRINT ========================================
async function renderPrintPreview() {
  $("#print-media").value = design.media_mm;
  // Same exact print render as everywhere else — what you see is what feeds out.
  renderPrintRender("#print-preview", "#print-ruler", "#print-tape-used", design);
  // Orientation badge from the measured render.
  const m = await api.post("/api/measure", design);
  if (m.ok) {
    const orient = m.length_px > m.width_px ? "portrait" : (m.length_px < m.width_px ? "landscape" : "square");
    $("#print-orient").innerHTML = `<span class="badge ${orient}">${orient}</span>`;
  }
}

$("#btn-print").onclick = async () => {
  design.media_mm = +$("#print-media").value;
  // Confirm with the actual tape length so the user knows before burning tape.
  const m = await api.post("/api/measure", design);
  const len = m.ok ? `${m.length_cm} cm (${m.length_in}″)` : "?";
  if (!confirm(`Print this label? It will use about ${len} of tape.`)) return;
  const body = { ...design, mode: $("#print-mode").value, cut: $("#print-cut").value };
  flash("#print-status", "printing… (hold tight, ~10–20 s)");
  const r = await api.post("/api/print", body);
  if (r.ok) {
    // Show the TRUE tape stats from the hardware before/after remain delta.
    const stats = [];
    if (r.remain_before != null) stats.push(`before ${r.remain_before}″`);
    if (r.tape_used_in != null) stats.push(`used ${r.tape_used_in}″ (${(r.tape_used_in*2.54).toFixed(1)} cm)`);
    if (r.remain_after != null) stats.push(`now ${r.remain_after}″`);
    flash("#print-status", "✓ printed — " + (stats.join(" · ") || `remaining ${r.remain_in ?? "?"}″`));
  } else {
    flash("#print-status", "✗ " + (r.error || ("state " + r.state)));
  }
  pollStatus();
};
$("#btn-reset").onclick = async () => {
  flash("#print-status", "resetting…");
  const r = await api.post("/api/reset", {});
  flash("#print-status", r.ok ? (r.wedged ? "⚠ " + r.hint : "device ready") : "✗ " + r.error);
  pollStatus();
};

// ============================ DEVICE =======================================
$("#btn-refresh-device").onclick = loadDevice;
async function loadDevice() {
  const d = await api.json("/api/device");
  const fmtRemain = d.remain_in != null ? `${d.remain_in}" (${d.remain_cm} cm)` : "?";
  const rows = [
    ["Host", d.host], ["Reachable", d.ok ? "yes" : "NO — " + (d.error || "")],
    ["State", d.state], ["Stage", d.stage], ["Error", d.error_field ?? d.error ?? "—"],
    ["Tape remaining", fmtRemain], ["Cassette type", d.cassette_type],
    ["Media", d.media_name || "?"], ["Online", d.online], ["Power", d.capacity != null ? d.capacity + "%" : "?"],
    ["Ready", d.ready ? "yes" : "no"], ["Total prints", d.total_prints], ["Last printed", d.last_printed || "—"],
  ];
  $("#device-table").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v ?? "—"}</td></tr>`).join("");
  $("#device-raw").textContent = d.raw || "(no status body)";
}

// ============================ SETTINGS =====================================
async function loadSettings() {
  const r = await api.json("/api/settings");
  settingsCache = r.settings;
  $("#set-host").value = r.settings.host;
  $("#set-media").value = r.settings.media_width;
  $("#set-mode").value = r.settings.mode;
  $("#set-cut").value = r.settings.cut;
  $("#set-bg").value = toHex(r.settings.background);
  $("#set-units").value = r.settings.units;
  design.media_mm = r.settings.media_width;
  design.background = r.settings.background;
  renderCustomColors();
}

// ---- custom color presets (Settings) ----------------------------------------
function renderCustomColors() {
  const box = $("#set-custom-colors");
  const customs = settingsCache.custom_colors || [];
  box.innerHTML = customs.length
    ? customs.map(c =>
        `<button type="button" class="swatch" title="${c} — click to remove"
           data-remove="${c}" style="background:${c}"></button>`).join("")
    : `<span class="muted" style="font-size:.8rem">none yet</span>`;
  box.querySelectorAll("[data-remove]").forEach(b => b.onclick = () => {
    settingsCache.custom_colors = (settingsCache.custom_colors || [])
      .filter(c => c !== b.dataset.remove);
    renderCustomColors();
  });
}
$("#set-custom-add").onclick = () => {
  const c = toHex($("#set-custom-pick").value);
  const list = settingsCache.custom_colors || (settingsCache.custom_colors = []);
  if (!list.map(x => x.toLowerCase()).includes(c.toLowerCase())) list.push(c);
  renderCustomColors();
};

$("#btn-save-settings").onclick = async () => {
  const body = {
    host: $("#set-host").value, media_width: +$("#set-media").value,
    mode: $("#set-mode").value, cut: $("#set-cut").value,
    font: $("#set-font").value || null, background: $("#set-bg").value, units: $("#set-units").value,
    custom_colors: settingsCache.custom_colors || [],
  };
  const r = await api.post("/api/settings", body);
  if (r.ok) { settingsCache = r.settings; flash("#settings-status", "saved"); pollStatus(); }
};

async function loadFonts() {
  const { fonts, legacy } = await api.json("/api/fonts");
  // fonts: [{name, has_bold, has_italic}]. Keep a name->meta map for the toggles.
  window.__fonts = fonts;
  window.__fontMeta = Object.fromEntries(fonts.map(f => [f.name, f]));
  window.__fontLegacy = legacy || {};   // raw "arial.ttf" -> family "Arial"
  // Prefer a real family (Arial) over the style-less "(default)" so Bold/Italic work
  // out of the box. If the user hasn't chosen one, adopt the default as the setting.
  if (!settingsCache.font) settingsCache.font = defaultFont();
  fillFontSelect($("#set-font"), normalizeFont(settingsCache.font));
}
// Best default family: Arial if installed, else the first available real family,
// else "" (the bitmap "(default)", which has no bold/italic).
function defaultFont() {
  const fonts = window.__fonts || [];
  if (window.__fontMeta && window.__fontMeta["Arial"]) return "Arial";
  const real = fonts.find(f => f.name !== "(default)");
  return real ? real.name : "";
}
// Map a stored font value (possibly a legacy .ttf filename) to a family name.
// __fontLegacy[file] = {family, bold, italic}.
function normalizeFont(font) {
  if (!font) return font;
  const m = (window.__fontLegacy || {})[font];
  return m ? m.family : font;
}
// Rewrite legacy .ttf font names on every text element of a loaded design so the
// family dropdown + Bold/Italic toggles line up. Older designs that stored e.g.
// "arialbd.ttf" become {font:"Arial", bold:true}. The bold/italic implied by the
// filename is OR-ed into any flags already present.
function migrateFonts(dl) {
  const legacy = window.__fontLegacy || {};
  (dl.elements || []).forEach(el => {
    if (el.type !== "text" || !el.font) return;
    const m = legacy[el.font];
    if (m) {
      el.font = m.family;
      el.bold = !!el.bold || m.bold;
      el.italic = !!el.italic || m.italic;
    }
  });
}
function fillFontSelect(sel, current) {
  if (!sel) return;
  const fonts = window.__fonts || [];
  sel.innerHTML = `<option value="">(default)</option>` +
    fonts.map(f => `<option ${f.name === current ? "selected" : ""}>${f.name}</option>`).join("");
}
// Whether a family supports a given style (default font has neither).
function fontHas(name, style) {
  const m = (window.__fontMeta || {})[name];
  return m ? (style === "bold" ? m.has_bold : m.has_italic) : false;
}

// ============================ ABOUT / HISTORY ==============================
async function loadAbout() {
  const a = await api.json("/api/about");
  const rows = [["Version", a.version], ["Python", a.python], ["Platform", a.platform],
    ["Hostname", a.hostname], ["Runtime dir", a.runtime_dir], ["Free memory", a.free_memory],
    ["Printer", a.printer_model]];
  $("#about-table").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
}
async function loadHistory() {
  const { history } = await api.json("/api/history");
  const ul = $("#history-list"); ul.innerHTML = "";
  if (!history.length) { ul.innerHTML = `<li class="muted">No prints yet.</li>`; return; }
  history.forEach(h => {
    const li = document.createElement("li");
    // Only show tape-used when we have the TRUE hardware figure (before/after remain
    // delta). Old entries that predate hardware stats have only a pixel estimate that
    // is unreliable for landscape labels (autofit blowup) — so we hide it entirely
    // rather than mislead.
    const usedBit = h.tape_used_cm != null
      ? ` · tape used ${h.tape_used_cm} cm (${h.tape_used_in}″)` : "";
    const orient = h.orientation
      ? `<span class="badge ${h.orientation}">${h.orientation}</span>` : "";
    const remainBits = (h.remain_before_in != null && h.remain_after_in != null)
      ? `<br><small class="muted">remaining ${h.remain_before_in}″ → ${h.remain_after_in}″</small>` : "";
    li.innerHTML = `
      <img class="h-thumb" src="${h.thumb}" alt="" loading="lazy">
      <div class="h-meta">
        <b>${escapeHtml(h.name) || "(untitled)"}</b> ${orient}<br>
        <small>${fmtTime(h.timestamp)} · ${h.media_mm} mm tape${usedBit}</small>
        ${remainBits}
      </div>
      <div class="h-actions"><button data-load>Load</button><button data-del>✕</button></div>`;
    li.querySelector("[data-load]").onclick = () => loadDesignIntoEditor(h.display_list);
    li.querySelector("[data-del]").onclick = async () => { await api.del("/api/history/" + h.id); li.remove(); };
    ul.appendChild(li);
  });
}

function loadDesignIntoEditor(dl) {
  if (!dl) return;
  design = { ...newDesign(), ...dl };
  migrateFonts(design);
  if (design.rotate == null) design.rotate = 0;
  selected = design.elements?.length ? 0 : null;
  $("#design-name").value = design.name || "";
  $("#edit-media").value = design.media_mm;
  $("#edit-length").value = design.length_px;
  syncRotateLabel();
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".panel").forEach(x => x.classList.remove("active"));
  $(`.tab[data-tab="edit"]`).classList.add("active");
  $("#tab-edit").classList.add("active");
  renderEditor();
}

// ---- utils ------------------------------------------------------------------
function flash(sel, msg) { const e = $(sel); if (e) e.textContent = msg; }
function escapeAttr(s) { return String(s).replace(/"/g, "&quot;"); }
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}
function toHex(c) {
  if (!c) return "#000000";
  c = String(c);                       // guard: callers sometimes pass non-strings
  if (c[0] === "#") return c;
  const named = { white: "#ffffff", black: "#000000", red: "#ff0000", green: "#008000", blue: "#0000ff", yellow: "#ffff00" };
  return named[c.toLowerCase()] || "#000000";
}
