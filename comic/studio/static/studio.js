// Comic Studio -- the operator's region review (2026-11-08).
// Plain JS, no build step. Every change autosaves to /api/page.
"use strict";

const LABELS = [
  ["dialogue", "#4c8dff"], ["thought", "#56ccf2"], ["narration", "#a86bff"],
  ["caption", "#d58bff"], ["shout", "#ff9f43"], ["sfx", "#ff5c5c"],
  ["logo", "#2ecc71"], ["watermark", "#a3e635"], ["credit", "#14b8a6"],
  ["keep", "#9aa1ad"],
];
// Labels past the ten digit keys, each on its own letter (the operator, 2026-11-14:
// keep 1-9 and 0 as they are). sfx_lama = an SFX translated and set like any other,
// but erased by LaMa at cleaning instead of Content-Aware Fill in Photoshop.
const EXTRA = [["sfx_lama", "#f472b6", "L"]];
const COLOR = Object.fromEntries([...LABELS, ...EXTRA.map(([l, c]) => [l, c])]);
// Every label button with its key: 1 ... 9, 0, then the letter keys.
const BUTTONS = [...LABELS.map(([l, c], k) => [l, c, String((k + 1) % 10)]), ...EXTRA];
// Display names only -- the saved label stays English (dialogue, sfx, ...).
const NAME = {
  dialogue: "Thoại", thought: "Suy nghĩ", narration: "Dẫn truyện", caption: "Chú thích",
  shout: "Hét", sfx: "SFX", logo: "Logo", watermark: "Watermark", credit: "Credit nhóm",
  keep: "Giữ nguyên", sfx_lama: "SFX xóa (LaMa)",
};
const nm = (l) => NAME[l] || l;
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

const S = {
  series: null, chapter: null, pages: [], page: null, data: null,
  regions: [], sel: null, multi: new Set(), zoom: null, lastLabel: "dialogue",
  undo: [], redo: [], saveTimer: null, saving: false, dirty: false,
};

// ---------------------------------------------------------------- api
async function api(path, body) {
  const r = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : {});
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
const qs = (o) => new URLSearchParams(o).toString();

function status(text, cls = "") { const s = $("status"); s.textContent = text; s.className = "status " + cls; }

// ---------------------------------------------------------------- loading
async function init() {
  const params = new URLSearchParams(location.search);
  const series = await api("/api/series");
  const sel = $("series");
  sel.innerHTML = series.map((s) => `<option>${esc(s.series)}</option>`).join("");
  S.seriesList = series;
  const want = params.get("series");
  if (want && series.some((s) => s.series === want)) sel.value = want;
  await pickSeries(params.get("chapter"), parseInt(params.get("page") || "0", 10));
  sel.onchange = () => pickSeries();
  $("chapter").onchange = () => pickChapter();
}

async function pickSeries(chapter, page) {
  await flush();
  S.series = $("series").value;
  const entry = S.seriesList.find((s) => s.series === S.series);
  const chapters = entry ? entry.chapters : [];
  $("chapter").innerHTML = chapters.map((c) => `<option>${esc(c)}</option>`).join("");
  if (chapter && chapters.includes(chapter)) $("chapter").value = chapter;
  await pickChapter(page);
}

async function pickChapter(page) {
  await flush();
  S.chapter = $("chapter").value;
  if (!S.chapter) return;
  const info = await api("/api/chapter?" + qs({ series: S.series, chapter: S.chapter }));
  S.pages = info.pages;
  renderPages();
  const first = S.pages.find((p) => p.page === page) || S.pages.find((p) => !p.reviewed) || S.pages[0];
  if (first) await loadPage(first.page);
}

async function loadPage(page) {
  await flush();
  S.data = await api("/api/page?" + qs({ series: S.series, chapter: S.chapter, page }));
  S.page = page;
  S.regions = S.data.regions.map((r) => ({ ...r }));
  S.sel = null; S.multi.clear(); S.undo = []; S.redo = [];
  history.replaceState(null, "", "?" + qs({ series: S.series, chapter: S.chapter, page }));
  const img = $("img");
  img.src = "/image?" + qs({ series: S.series, chapter: S.chapter, page, v: S.data.v || 0 });
  $("pageLabel").textContent = `p${String(page).padStart(3, "0")} / ${S.pages.length}`;
  if (S.zoom === null) fitWidth(); else applyZoom();
  renderPages(); draw(); renderSide();
  $("stage").scrollTop = 0;
  status(S.data.reviewed ? "đã duyệt" : "chưa duyệt", S.data.reviewed ? "ok" : "");
}

// ---------------------------------------------------------------- saving
function changed() {
  S.dirty = true; status("chưa lưu…");
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(save, 700);
}
function snapshot() { S.undo.push(JSON.stringify(S.regions)); if (S.undo.length > 200) S.undo.shift(); S.redo = []; }

async function save(extra = {}) {
  clearTimeout(S.saveTimer);
  if (!S.dirty && !("reviewed" in extra)) return;
  if (S.saving) { S.saveTimer = setTimeout(() => save(extra), 200); return; }
  S.saving = true; S.dirty = false; status("đang lưu…");
  const page = S.page;
  try {
    const out = await api("/api/page", {
      series: S.series, chapter: S.chapter, page,
      regions: S.regions.map(({ id, label, bbox, text, angle }) => ({ id, label, bbox, text, angle: angle || 0 })),
      ...extra,
    });
    if (S.page === page) {
      // server assigns ids to new boxes and re-orders; keep the selection by position
      const selBox = S.sel !== null ? JSON.stringify(S.regions[S.sel]?.bbox) : null;
      if (!S.dirty) {
        S.regions = out.regions.map((r) => ({ ...r }));
        S.multi.clear();   // the server re-orders boxes, so indices may move
        S.sel = selBox ? S.regions.findIndex((r) => JSON.stringify(r.bbox) === selBox) : null;
        if (S.sel < 0) S.sel = null;
      }
      S.data.reviewed = out.reviewed;
      draw(); renderSide();
    }
    const p = S.pages.find((x) => x.page === page);
    if (p) { p.regions = out.regions.length; p.reviewed = out.reviewed; p.edited = true; renderPages(); }
    status(out.reviewed ? "đã lưu · đã duyệt" : "đã lưu", "ok");
  } catch (e) {
    S.dirty = true; status("lưu lỗi: " + e.message, "err");
  } finally { S.saving = false; }
}
async function flush() { if (S.dirty) await save(); }

// ---------------------------------------------------------------- zoom
function fitWidth() {
  if (!S.data) return;
  const w = $("stage").clientWidth - 24;
  S.zoom = Math.min(1.5, w / S.data.width);
  applyZoom();
}
function applyZoom() {
  if (!S.data) return;
  const c = $("canvas");
  c.style.width = Math.round(S.data.width * S.zoom) + "px";
  c.style.height = Math.round(S.data.height * S.zoom) + "px";
  const ov = $("ov");
  ov.setAttribute("viewBox", `0 0 ${S.data.width} ${S.data.height}`);
  $("zoomLabel").textContent = Math.round(S.zoom * 100) + "%";
  draw();
}
function zoomBy(f) {
  const st = $("stage"); const cy = (st.scrollTop + st.clientHeight / 2) / S.zoom;
  S.zoom = Math.max(0.1, Math.min(4, S.zoom * f)); applyZoom();
  st.scrollTop = cy * S.zoom - st.clientHeight / 2;
}

// ---------------------------------------------------------------- drawing
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function draw() {
  const ov = $("ov"); ov.innerHTML = "";
  if (!S.data) return;
  const alpha = $("boxAlpha").value / 100;
  const sw = 2 / S.zoom, hs = 7 / S.zoom;
  S.regions.forEach((r, i) => {
    const [x1, y1, x2, y2] = r.bbox, c = COLOR[r.label] || "#fff";
    const on = isSel(i), ang = r.angle || 0;
    const g = el("g", { "data-i": i, transform: ang ? `rotate(${ang} ${(x1 + x2) / 2} ${(y1 + y2) / 2})` : "" }, ov);
    el("rect", {
      x: x1, y: y1, width: x2 - x1, height: y2 - y1, class: "box", "data-i": i,
      fill: c, "fill-opacity": on ? Math.min(0.5, alpha + 0.12) : alpha,
      stroke: S.multi.size > 1 && on ? "#ffffff" : c, "stroke-width": on ? sw * 1.8 : sw,
      "stroke-dasharray": r.label === "keep" ? `${6 / S.zoom} ${4 / S.zoom}` : "",
    }, g);
    if ($("showText").checked) {
      const t = el("text", { x: x1 + 2 / S.zoom, y: y1 - 4 / S.zoom, fill: c,
        "font-size": 12 / S.zoom, "stroke-width": 3 / S.zoom }, g);
      t.textContent = `${i + 1} ${nm(r.label)}${r.confidence !== null && r.confidence !== undefined && r.confidence < 0.72 ? " ⚠" : ""}`;
    }
    if (i === S.sel && S.multi.size <= 1) {
      for (const [hx, hy, h] of handles(r.bbox)) {
        el("rect", { x: hx - hs / 2, y: hy - hs / 2, width: hs, height: hs, class: "handle",
          "data-h": h, "data-i": i, "stroke-width": 1 / S.zoom, style: `cursor:${h}-resize` }, g);
      }
      // Rotate handle: a knob above the top edge (the operator, 2026-11-09).
      const mx = (x1 + x2) / 2, ry = y1 - 24 / S.zoom;
      el("line", { x1: mx, y1: y1, x2: mx, y2: ry, stroke: "#fff", "stroke-width": 1 / S.zoom, "pointer-events": "none" }, g);
      el("circle", { cx: mx, cy: ry, r: hs * 0.75, class: "handle rot", "data-rot": i, "data-i": i,
        "stroke-width": 1 / S.zoom, style: "cursor:grab" }, g);
    }
  });
  if (marquee) {
    el("rect", { x: marquee[0], y: marquee[1], width: marquee[2] - marquee[0], height: marquee[3] - marquee[1],
      fill: "#4c8dff", "fill-opacity": 0.12, stroke: "#4c8dff", "stroke-width": 1.5 / S.zoom,
      "stroke-dasharray": `${5 / S.zoom} ${3 / S.zoom}`, "pointer-events": "none" }, ov);
  }
}
function handles([x1, y1, x2, y2]) {
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  return [[x1, y1, "nw"], [mx, y1, "n"], [x2, y1, "ne"], [x2, my, "e"],
          [x2, y2, "se"], [mx, y2, "s"], [x1, y2, "sw"], [x1, my, "w"]];
}

// ---------------------------------------------------------------- side panel
function renderPages() {
  $("pages").innerHTML = S.pages.map((p) =>
    `<button data-p="${p.page}" class="${p.page === S.page ? "cur" : ""} ${p.reviewed ? "done" : ""}"
      title="${p.regions} khung${p.edited ? ", đã sửa" : ""}">
      <span>${String(p.page).padStart(3, "0")}</span><span class="dot"></span></button>`).join("");
  const cur = $("pages").querySelector(".cur");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}
function renderSide() {
  const box = $("sel");
  const r = S.sel !== null ? S.regions[S.sel] : null;
  if (S.multi.size > 1) {
    box.innerHTML = `
      <div class="cardhead">${S.multi.size} khung đang chọn</div>
      <div class="labels">${BUTTONS.map(([l, c, key]) =>
        `<button style="--c:${c}" data-label="${l}">${nm(l)}<kbd>${key}</kbd></button>`).join("")}</div>
      <button id="delSel" class="danger">Xóa ${S.multi.size} khung (Delete)</button>
      <div class="muted" style="margin-top:6px">Ctrl+click để thêm/bỏ một khung · Esc để bỏ chọn</div>`;
    box.querySelectorAll("[data-label]").forEach((b) => b.onclick = () => setLabel(b.dataset.label));
    $("delSel").onclick = del;
  } else if (!r) {
    box.innerHTML = `<div class="muted">Chọn một khung, hoặc kéo chuột trên trang để vẽ khung mới.
      Khung mới lấy loại dùng gần nhất: <b style="color:${COLOR[S.lastLabel]}">${nm(S.lastLabel)}</b>.</div>`;
  } else {
    const low = r.confidence !== null && r.confidence !== undefined && r.confidence < 0.72;
    box.innerHTML = `
      <div class="cardhead">#${S.sel + 1} <span class="muted">${esc(r.id || "khung mới")}</span></div>
      <div class="labels">${BUTTONS.map(([l, c, key]) =>
        `<button style="--c:${c}" data-label="${l}" class="${l === r.label ? "on" : ""}">${nm(l)}<kbd>${key}</kbd></button>`).join("")}</div>
      <textarea id="txt" placeholder="chữ gốc (OCR) — khung mới có thể để trống, sẽ được chép lại ở bước kiểm tra">${esc(r.text || "")}</textarea>
      <div class="meta">
        <span>${r.bbox.join(", ")}</span>
        <label class="ang">xoay <input id="ang" type="number" step="1" min="-180" max="180" value="${+(r.angle || 0).toFixed(1)}">°</label>
        ${r.confidence !== null && r.confidence !== undefined ? `<span class="${low ? "warn" : ""}">độ tin cậy ${r.confidence.toFixed(2)}</span>` : ""}
        ${r.edited ? "<span>đã sửa khung</span>" : ""}${r.reviewed ? "<span>chữ đã kiểm</span>" : ""}
      </div>`;
    box.querySelectorAll("[data-label]").forEach((b) => b.onclick = () => setLabel(b.dataset.label));
    const t = $("txt");
    t.oninput = () => { r.text = t.value; changed(); renderList(); };
    t.onfocus = () => snapshot();
    const a = $("ang");
    a.onfocus = () => snapshot();
    a.onchange = () => { r.angle = normAngle(+a.value || 0); changed(); draw(); };
  }
  renderList();
}
function renderList() {
  $("count").textContent = `(${S.regions.length})`;
  $("list").innerHTML = S.regions.map((r, i) =>
    `<li data-i="${i}" style="--c:${COLOR[r.label]}" class="${isSel(i) ? "cur" : ""}">
      <span class="k">${i + 1} ${nm(r.label)}</span><span class="t">${esc(r.text || "—")}</span></li>`).join("");
}

// ---------------------------------------------------------------- editing
// Multiple selection (the operator, 2026-11-08): S.multi holds every selected
// index when more than one box is selected; S.sel is the one last clicked.
function isSel(i) { return i === S.sel || S.multi.has(i); }
function selected() {
  if (S.multi.size) return [...S.multi];
  return S.sel === null ? [] : [S.sel];
}
function toggle(i) {
  if (!S.multi.size && S.sel !== null) S.multi.add(S.sel);
  if (S.multi.has(i)) { S.multi.delete(i); S.sel = S.multi.size ? [...S.multi].pop() : null; }
  else { S.multi.add(i); S.sel = i; }
  if (S.multi.size === 1) { S.sel = [...S.multi][0]; S.multi.clear(); }
  draw(); renderSide();
}
function selectMany(list) {
  S.multi = new Set(list);
  S.sel = list.length ? list[list.length - 1] : null;
  if (S.multi.size <= 1) S.multi.clear();
  draw(); renderSide();
}
function select(i, scroll) {
  S.multi.clear();
  S.sel = i; draw(); renderSide();
  if (scroll && i !== null) {
    const st = $("stage"), [, y1, , y2] = S.regions[i].bbox;
    const top = y1 * S.zoom, bot = y2 * S.zoom;
    if (top < st.scrollTop || bot > st.scrollTop + st.clientHeight) st.scrollTop = top - 80;
  }
}
function setLabel(label) {
  const sel = selected();
  if (!sel.length) { S.lastLabel = label; renderSide(); return; }
  snapshot(); sel.forEach((i) => { S.regions[i].label = label; }); S.lastLabel = label;
  changed(); draw(); renderSide();
}
function del() {
  const sel = selected().sort((a, b) => b - a);
  if (!sel.length) return;
  snapshot(); sel.forEach((i) => S.regions.splice(i, 1));
  S.sel = null; S.multi.clear();
  changed(); draw(); renderSide();
}
function nudge(dx, dy) {
  if (S.sel === null) return;
  snapshot(); const b = S.regions[S.sel].bbox;
  const w = b[2] - b[0], h = b[3] - b[1];
  b[0] = clamp(b[0] + dx, 0, S.data.width - w); b[1] = clamp(b[1] + dy, 0, S.data.height - h);
  b[2] = b[0] + w; b[3] = b[1] + h;
  changed(); draw(); renderSide();
}
function duplicate() {
  if (S.sel === null) return;
  snapshot(); const r = S.regions[S.sel];
  const b = r.bbox.map((v, k) => v + 12);
  S.regions.push({ id: null, label: r.label, bbox: [b[0], b[1], Math.min(b[2], S.data.width), Math.min(b[3], S.data.height)], text: "", edited: true, angle: r.angle || 0 });
  S.sel = S.regions.length - 1; changed(); draw(); renderSide();
}
function undoRedo(from, to) {
  if (!from.length) return;
  to.push(JSON.stringify(S.regions)); S.regions = JSON.parse(from.pop());
  S.sel = null; S.multi.clear(); changed(); draw(); renderSide();
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const normAngle = (a) => { a = ((a + 180) % 360 + 360) % 360 - 180; return Math.abs(a) < 0.05 ? 0 : Math.round(a * 10) / 10; };
// Upright bounds of a (possibly tilted) box -- for the rubber band.
function bounds(r) {
  const [x1, y1, x2, y2] = r.bbox, a = (r.angle || 0) * Math.PI / 180;
  if (!a) return r.bbox;
  const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2, c = Math.cos(a), s = Math.sin(a);
  const pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]].map(([x, y]) =>
    [cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c]);
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
// Turn the selected boxes by d degrees; null straightens them.
function rotateSel(d) {
  const sel = selected(); if (!sel.length) return;
  snapshot();
  sel.forEach((i) => { S.regions[i].angle = d === null ? 0 : normAngle((S.regions[i].angle || 0) + d); });
  changed(); draw(); renderSide();
}

// ---------------------------------------------------------------- mouse
function pt(e) {
  const r = $("ov").getBoundingClientRect();
  return [clamp((e.clientX - r.left) / S.zoom, 0, S.data.width), clamp((e.clientY - r.top) / S.zoom, 0, S.data.height)];
}
let drag = null;
let marquee = null;
$("ov").addEventListener("mousedown", (e) => {
  if (e.button !== 0 || !S.data) return;
  e.preventDefault(); document.activeElement?.blur();
  const [x, y] = pt(e), t = e.target;
  const adding = e.ctrlKey || e.metaKey || e.shiftKey;
  if (adding && t.dataset.i !== undefined && !t.dataset.h) {
    toggle(+t.dataset.i); return;                 // Ctrl/Shift+click: add or remove
  }
  if (e.shiftKey && t.dataset.i === undefined) {
    drag = { mode: "marquee", x, y, keep: new Set(selected()) };   // Shift+drag: rubber band
    marquee = [x, y, x, y]; return;
  }
  if (t.dataset.rot !== undefined) {
    const i = +t.dataset.rot, [x1, y1, x2, y2] = S.regions[i].bbox;
    snapshot(); drag = { mode: "rotate", i, cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, x, y, moved: false };
    return;
  }
  if (t.dataset.h) {
    snapshot(); drag = { mode: "resize", h: t.dataset.h, i: +t.dataset.i, start: [...S.regions[+t.dataset.i].bbox], x, y };
  } else if (t.dataset.i !== undefined) {
    const i = +t.dataset.i; select(i);
    snapshot(); drag = { mode: "move", i, start: [...S.regions[i].bbox], x, y, moved: false };
  } else {
    select(null); drag = { mode: "new", x, y, i: null };
  }
});
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  const [x, y] = pt(e), dx = x - drag.x, dy = y - drag.y;
  if (drag.mode === "marquee") {
    marquee = [Math.min(drag.x, x), Math.min(drag.y, y), Math.max(drag.x, x), Math.max(drag.y, y)];
    draw(); return;
  }
  if (drag.mode === "new") {
    if (drag.i === null) {
      if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
      snapshot();
      S.regions.push({ id: null, label: S.lastLabel, bbox: [x, y, x, y], text: "", edited: true });
      drag.i = S.regions.length - 1; S.sel = drag.i;
    }
    S.regions[drag.i].bbox = [Math.min(drag.x, x), Math.min(drag.y, y), Math.max(drag.x, x), Math.max(drag.y, y)].map(Math.round);
  } else if (drag.mode === "move") {
    const [a, b, c, d] = drag.start, w = c - a, h = d - b;
    const nx = clamp(a + dx, 0, S.data.width - w), ny = clamp(b + dy, 0, S.data.height - h);
    S.regions[drag.i].bbox = [nx, ny, nx + w, ny + h].map(Math.round);
    drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 1;
  } else if (drag.mode === "rotate") {
    let deg = Math.atan2(y - drag.cy, x - drag.cx) * 180 / Math.PI + 90;
    if (e.shiftKey) deg = Math.round(deg / 15) * 15;
    S.regions[drag.i].angle = normAngle(deg);
    drag.moved = true; renderSide();
  } else {
    // Resize in the box's own (tilted) frame, keeping the opposite edge in place.
    let [a, b, c, d] = drag.start; const h = drag.h;
    const t = (S.regions[drag.i].angle || 0) * Math.PI / 180, co = Math.cos(t), si = Math.sin(t);
    const lx = dx * co + dy * si, ly = -dx * si + dy * co;
    if (h.includes("w")) a += lx; if (h.includes("e")) c += lx;
    if (h.includes("n")) b += ly; if (h.includes("s")) d += ly;
    const [a0, b0, c0, d0] = drag.start;
    const ox = (a + c) / 2 - (a0 + c0) / 2, oy = (b + d) / 2 - (b0 + d0) / 2;
    const cx = (a0 + c0) / 2 + ox * co - oy * si, cy = (b0 + d0) / 2 + ox * si + oy * co;
    const w = Math.abs(c - a), hh = Math.abs(d - b);
    S.regions[drag.i].bbox = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2].map(Math.round);
  }
  draw();
});
window.addEventListener("mouseup", () => {
  if (!drag) return;
  const d = drag; drag = null;
  if (d.mode === "marquee") {
    const [a, b, c, e2] = marquee; marquee = null;
    const hit = S.regions.map((r, i) => i).filter((i) => {
      const [x1, y1, x2, y2] = bounds(S.regions[i]);
      return x1 < c && x2 > a && y1 < e2 && y2 > b;       // any overlap counts
    });
    selectMany([...new Set([...d.keep, ...hit])]);
    return;
  }
  if (d.mode === "move" && !d.moved) { S.undo.pop(); return; }
  if (d.mode === "rotate") { if (!d.moved) S.undo.pop(); else changed(); select(d.i); return; }
  if (d.mode === "new" && d.i === null) return;
  const b = S.regions[d.i].bbox;
  if (b[2] - b[0] < 5 || b[3] - b[1] < 5) { S.regions.splice(d.i, 1); S.undo.pop(); S.sel = null; draw(); renderSide(); return; }
  changed(); select(d.i);
});
$("stage").addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return; e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15);
}, { passive: false });

// ---------------------------------------------------------------- keys
window.addEventListener("keydown", (e) => {
  const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement?.tagName);
  if (e.key === "Escape") { $("helpbox").hidden = true; document.activeElement?.blur(); select(null); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
  if (typing) return;
  const k = e.key;
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? undoRedo(S.redo, S.undo) : undoRedo(S.undo, S.redo); return; }
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "y") { e.preventDefault(); undoRedo(S.redo, S.undo); return; }
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "d") { e.preventDefault(); duplicate(); return; }
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "a") {
    e.preventDefault(); selectMany(S.regions.map((r, i) => i)); return;   // every box on the page
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.code === "BracketLeft" || e.code === "BracketRight") {
    e.preventDefault(); rotateSel((e.code === "BracketLeft" ? -1 : 1) * (e.shiftKey ? 5 : 1)); return;
  }
  if (e.code === "Backslash") { e.preventDefault(); rotateSel(null); return; }
  if (/^[0-9]$/.test(k)) { setLabel(LABELS[(+k + 9) % 10][0]); return; }
  const extra = EXTRA.find(([, , key]) => key.toLowerCase() === k.toLowerCase());
  if (extra) { setLabel(extra[0]); return; }
  if (k === "Delete" || k === "Backspace") { e.preventDefault(); del(); return; }
  const step = e.shiftKey ? 10 : 1;
  if (S.sel !== null && k.startsWith("Arrow")) {
    e.preventDefault();
    nudge(k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0, k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0);
    return;
  }
  if (k === "Tab") {
    e.preventDefault(); if (!S.regions.length) return;
    const n = S.regions.length, cur = S.sel === null ? (e.shiftKey ? 0 : -1) : S.sel;
    select((cur + (e.shiftKey ? -1 : 1) + n) % n, true); return;
  }
  if (k === "PageDown" || k === "d") { e.preventDefault(); step_page(1); return; }
  if (k === "PageUp" || k === "a") { e.preventDefault(); step_page(-1); return; }
  if (k === "r") { markReviewed(); return; }
  if (k === "+" || k === "=") { zoomBy(1.2); return; }
  if (k === "-") { zoomBy(1 / 1.2); return; }
  if (k === "f") { fitWidth(); return; }
  if (k === "?") { $("helpbox").hidden = false; }
});

function step_page(d) {
  const i = S.pages.findIndex((p) => p.page === S.page);
  const n = S.pages[i + d]; if (n) loadPage(n.page);
}
async function reviewAll() {
  const left = S.pages.filter((p) => !p.reviewed).length;
  if (!left) { status("cả chương đã duyệt ✓", "ok"); return; }
  if (!confirm(`Đánh dấu đã duyệt cả chương (${left} trang chưa duyệt)?`)) return;
  await flush();
  try {
    const out = await api("/api/chapter-reviewed", { series: S.series, chapter: S.chapter, reviewed: true });
    const done = new Set(out.reviewed);
    S.pages.forEach((p) => { p.reviewed = done.has(p.page); });
    if (S.data) S.data.reviewed = done.has(S.page);
    renderPages(); status("đã duyệt hết chương ✓", "ok");
  } catch (e) { status("lỗi: " + e.message, "err"); }
}
async function markReviewed() {
  await save({ reviewed: true });
  const i = S.pages.findIndex((p) => p.page === S.page);
  const next = S.pages.slice(i + 1).find((p) => !p.reviewed) || S.pages[i + 1];
  if (next) loadPage(next.page);
  else status("đã duyệt hết chương ✓", "ok");
}

// ---------------------------------------------------------------- wiring
$("pages").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) loadPage(+b.dataset.p); };
$("list").onclick = (e) => {
  const li = e.target.closest("[data-i]"); if (!li) return;
  if (e.ctrlKey || e.metaKey || e.shiftKey) toggle(+li.dataset.i); else select(+li.dataset.i, true);
};
$("reviewAll").onclick = reviewAll;
$("prev").onclick = () => step_page(-1);
$("next").onclick = () => step_page(1);
$("zoomIn").onclick = () => zoomBy(1.2);
$("zoomOut").onclick = () => zoomBy(1 / 1.2);
$("zoomFit").onclick = fitWidth;
$("showText").onchange = draw;
$("boxAlpha").oninput = draw;
$("reviewed").onclick = markReviewed;
$("help").onclick = () => { $("helpbox").hidden = false; };
$("helpClose").onclick = () => { $("helpbox").hidden = true; };
window.addEventListener("beforeunload", (e) => { if (S.dirty) { save(); e.preventDefault(); } });

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

init().catch((e) => status("tải lỗi: " + e.message, "err"));
