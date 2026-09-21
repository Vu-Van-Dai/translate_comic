/**
 * Capture orchestration.
 *
 * Two modes:
 *   urls    - the reader serves real images; download them directly.
 *   capture - the reader paints its pages; photograph them with the browser's
 *             own screenshot API (chrome.tabs.captureVisibleTab).
 *
 * Panels taller than the viewport are captured in slices and stitched. Nothing
 * here reads pixels out of a page <canvas>.
 */

const CAPTURE_THROTTLE_MS = 600;   // captureVisibleTab is rate-limited
const SLICE_OVERLAP = 2;           // px, hides seam rounding

let running = false;
let cancelRequested = false;

function safeName(s, fallback) {
  const cleaned = String(s || '').replace(/[<>:"/\\|?*\x00-\x1f]/g, ' ')
    .replace(/\s+/g, ' ').trim();
  return cleaned || fallback;
}

function pad(n) { return String(n).padStart(3, '0'); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function progress(payload) {
  chrome.runtime.sendMessage({ type: 'progress', ...payload }).catch(() => {});
}

function ask(tabId, message) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, message, (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        resolve(resp || { ok: false, error: 'no response' });
      }
    });
  });
}

/** Blob -> data: URL. Service workers have no URL.createObjectURL. */
async function blobToDataUrl(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < buf.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, buf.subarray(i, i + CHUNK));
  }
  return `data:${blob.type};base64,${btoa(binary)}`;
}

async function download(dataUrl, path) {
  return new Promise((resolve, reject) => {
    chrome.downloads.download(
      { url: dataUrl, filename: path, conflictAction: 'uniquify', saveAs: false },
      (id) => (chrome.runtime.lastError
        ? reject(new Error(chrome.runtime.lastError.message))
        : resolve(id)),
    );
  });
}

/** Photograph one panel, stitching vertical slices when it exceeds the viewport. */
async function capturePanel(tabId, windowId, index, settle) {
  let placed = await ask(tabId, { type: 'position', index, sliceTop: 0, settle });
  if (!placed.ok) throw new Error(placed.error || 'could not position panel');

  const dpr = placed.dpr || 1;
  const panelH = placed.rect.h;
  const viewH = placed.viewport.h;
  const usable = Math.max(100, viewH - Math.max(0, placed.rect.y));

  const sliceCount = panelH <= usable ? 1 : Math.ceil(panelH / (viewH * 0.9));
  const out = new OffscreenCanvas(
    Math.round(placed.rect.w * dpr),
    Math.round(panelH * dpr),
  );
  const ctx = out.getContext('2d');
  let written = 0;

  for (let s = 0; s < sliceCount; s++) {
    if (cancelRequested) throw new Error('cancelled');
    if (s > 0) {
      placed = await ask(tabId, {
        type: 'position', index,
        sliceTop: Math.max(0, s * viewH * 0.9 - SLICE_OVERLAP),
        settle,
      });
      if (!placed.ok) throw new Error(placed.error || 'lost the panel mid-capture');
    }
    await sleep(CAPTURE_THROTTLE_MS);
    const shot = await chrome.tabs.captureVisibleTab(windowId, { format: 'png' });
    const bitmap = await createImageBitmap(await (await fetch(shot)).blob());

    // Intersection of the panel with the visible viewport, in device pixels.
    const r = placed.rect;
    const visTop = Math.max(0, r.y);
    const visBottom = Math.min(placed.viewport.h, r.y + r.h);
    const visH = visBottom - visTop;
    if (visH <= 0) { bitmap.close(); continue; }

    const sx = Math.max(0, Math.round(r.x * dpr));
    const sy = Math.round(visTop * dpr);
    const sw = Math.round(r.w * dpr);
    const sh = Math.round(visH * dpr);
    const dy = Math.round((visTop - r.y) * dpr);

    ctx.drawImage(bitmap, sx, sy, sw, sh, 0, dy, sw, sh);
    written = Math.max(written, dy + sh);
    bitmap.close();
  }

  if (written === 0) throw new Error('panel never became visible');
  return out.convertToBlob({ type: 'image/png' });
}

/** Is this screenshot actually artwork, or an unpainted placeholder? */
async function looksPainted(blob) {
  try {
    const bitmap = await createImageBitmap(blob);
    const w = Math.min(160, bitmap.width);
    const h = Math.min(160, bitmap.height);
    const c = new OffscreenCanvas(w, h);
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(bitmap, 0, 0, w, h);
    bitmap.close();
    const d = g.getImageData(0, 0, w, h).data;
    const seen = new Set();
    for (let i = 0; i < d.length; i += 16) {
      seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
      if (seen.size >= 24) return true;
    }
    return seen.size >= 24;
  } catch {
    return true; // never block a download on the check itself failing
  }
}

/** Fetch a URL with the page's own session. */
async function fetchImage(url) {
  const r = await fetch(url, { credentials: 'include' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.blob();
}

//: A comic page is big in both axes. An avatar is 400x400, a site icon 68x68
//: and a banner 1600x592 -- all of which sailed past an earlier "min 400"
//: test and were saved as pages. Require real page area AND a real short side.
const MIN_PAGE_SIDE = 600;
const MIN_PAGE_AREA = 600000;

function looksLikePage(w, h) {
  return Math.min(w, h) >= MIN_PAGE_SIDE && w * h >= MIN_PAGE_AREA;
}

/**
 * Which host, if any, is actually serving comic pages?
 *
 * Decided by decoding samples rather than by counting entries or comparing
 * byte sizes: cross-origin timing entries report size 0, and an avatar CDN
 * looks exactly like a page CDN until you open one of its images.
 */
async function probeResources(hostMap) {
  const hosts = Object.entries(hostMap || {})
    .sort((a, b) => b[1].length - a[1].length);
  const tried = [];

  for (const [host, urls] of hosts) {
    // Check SEVERAL samples. Breaking on the first non-page image disqualifies
    // the whole CDN over a single cover or thumbnail that happens to sort first.
    const sizes = [];
    for (const url of urls.slice(0, 6)) {
      try {
        const blob = await fetchImage(url);
        const bitmap = await createImageBitmap(blob);
        const [w, h] = [bitmap.width, bitmap.height];
        bitmap.close();
        if (looksLikePage(w, h)) {
          return { usable: true, host, w, h, count: urls.length };
        }
        sizes.push(`${w}x${h}`);
      } catch { /* try the next url on this host */ }
    }
    if (sizes.length) tried.push(`${host}: ${sizes.slice(0, 3).join(', ')}`);
  }
  return { usable: false, tried };
}

async function digest(blob) {
  const buf = await blob.arrayBuffer();
  const hash = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, '0')).join('');
}


/** Capture every panel currently in the DOM. Returns how many were saved. */
async function captureCurrent(ctx) {
  const scan = await ask(ctx.tabId, { type: 'scan', prime: false });
  if (!scan.ok) throw new Error(scan.error || 'scan failed');

  // Resource mode enumerates downloaded originals, not on-screen panels.
  if (ctx.mode === 'resources') {
    const fresh = await ask(ctx.tabId, { type: 'resources' });
    const map = (fresh.ok && fresh.resourceHosts) || {};
    ctx.resources = map[ctx.host] || [];
  }
  const count = ctx.mode === 'resources' ? ctx.resources.length : scan.total;

  // Screenshots include whatever floats over the page, so the reader's toolbar
  // and breadcrumb are hidden for the duration and restored afterwards.
  if (ctx.mode === 'capture' && ctx.zoom > 0 && !ctx.zoomed) {
    const z = await ask(ctx.tabId, { type: 'zoomIn', steps: ctx.zoom });
    ctx.zoomed = true;
    progress({ ...ctx.stat(), note: z.applied
      ? `zoomed ${z.applied} step(s), panel now ${Math.round(z.width || 0)}px`
      : `no zoom applied (${z.reason || 'unknown'})` });
    await sleep(600);
  }

  if (ctx.mode === 'capture') {
    const r = await ask(ctx.tabId, { type: 'hideUI' });
    if (r.ok && r.hidden) progress({ ...ctx.stat(), note: `hid ${r.hidden} overlay(s)` });
    await sleep(250);
  }

  for (let i = 0; i < count; i++) {
    if (cancelRequested) break;
    try {
      let blob;
      if (ctx.mode === 'resources') {
        const url = ctx.resources[i];
        // Each spread re-reports every resource loaded so far; without this the
        // work would grow quadratically with the number of spreads.
        if (!url || ctx.doneUrls.has(url)) continue;
        ctx.doneUrls.add(url);
        blob = await fetchImage(url);
        const bm = await createImageBitmap(blob);
        const pageish = looksLikePage(bm.width, bm.height);
        bm.close();
        if (!pageish) { continue; }   // an icon that shares the CDN
      } else if (ctx.mode === 'urls') {
        const url = scan.urls[i];
        if (!url) { ctx.failed++; continue; }
        blob = await fetchImage(url);
      } else {
        blob = await capturePanel(ctx.tabId, ctx.windowId, i, ctx.settle);
        if (!(await looksPainted(blob))) {
          ctx.blank++;
          progress({ ...ctx.stat(), note: 'blank, skipped' });
          continue;
        }
      }

      const key = await digest(blob);
      if (ctx.seen.has(key)) {
        progress({ ...ctx.stat(), note: 'already captured, skipped' });
        continue;
      }
      ctx.seen.add(key);

      const ext = ctx.mode === 'capture'
        ? 'png'
        : ((blob.type.split('/')[1] || 'jpg').replace('jpeg', 'jpg').replace('+xml', ''));
      await download(await blobToDataUrl(blob), `${ctx.dir}/${pad(ctx.next)}.${ext}`);
      ctx.next++;
      ctx.saved++;
      progress(ctx.stat());
    } catch (e) {
      ctx.failed++;
      progress({ ...ctx.stat(), note: String(e.message || e) });
    }
  }
  if (ctx.mode === 'capture') {
    await ask(ctx.tabId, { type: 'showUI' });
  }
  return scan;
}


async function run(opts) {
  const { tabId, windowId, series, chapter, mode, settle } = opts;
  running = true;
  cancelRequested = false;

  const dir = `comic-raw/${safeName(series, 'Series')}/${safeName(chapter, 'Chapter')}`;

  const ctx = {
    tabId, windowId, mode, settle, dir,
    next: Math.max(1, opts.startAt | 0 || 1),
    resources: [],
    host: opts.host || '',
    zoom: opts.zoom | 0,
    zoomed: false,
    doneUrls: new Set(),
    saved: 0, blank: 0, failed: 0, spread: 0,
    seen: new Set(),
    stat() {
      return {
        saved: this.saved, blank: this.blank, failed: this.failed,
        index: this.spread, last: opts.spreads || 1,
      };
    },
  };

  // A paged reader holds only the current spread in the DOM, so capturing it
  // once yields two pages and renumbers from 1 on the next run. Walk the pager
  // instead, counting straight through.
  const limit = Math.max(1, opts.spreads | 0 || 1);
  for (let s = 0; s < limit; s++) {
    if (cancelRequested) break;
    ctx.spread = s + 1;
    await captureCurrent(ctx);
    if (s + 1 >= limit) break;

    const moved = await ask(tabId, { type: 'advance', settle });
    if (!moved.ok) {
      progress({ ...ctx.stat(), note: moved.leftChapter
        ? 'stopped: that control jumps to the next chapter, not the next page'
        : 'reached the end (pager did not advance)' });
      break;
    }
    await sleep(Math.max(300, settle));
  }

  if (ctx.zoomed) await ask(tabId, { type: 'zoomReset' });

  running = false;
  progress({
    done: true, saved: ctx.saved, blank: ctx.blank, failed: ctx.failed,
    dir, cancelled: cancelRequested, nextNumber: ctx.next,
  });
  return { saved: ctx.saved, blank: ctx.blank, failed: ctx.failed, dir, nextNumber: ctx.next };
}

chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg.type === 'probe') {
    probeResources(msg.hosts || {})
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
    return true;
  }
  if (msg.type === 'start') {
    if (running) { sendResponse({ ok: false, error: 'already running' }); return true; }
    run(msg.opts)
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((e) => { running = false; sendResponse({ ok: false, error: String(e.message || e) }); });
    return true;
  }
  if (msg.type === 'cancel') { cancelRequested = true; sendResponse({ ok: true }); return true; }
  return false;
});
