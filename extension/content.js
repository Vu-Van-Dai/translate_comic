/**
 * Page-side logic: find the panels, find whatever actually scrolls, and put a
 * requested panel on screen so the background worker can photograph it.
 *
 * Deliberately does NOT read pixels out of any <canvas>. Where a reader paints
 * its pages, they are captured with the browser's own screenshot API, the same
 * way a person would capture them. See docs/acquisition.md.
 */

(() => {
  if (window.__comicCaptureInstalled) return;
  window.__comicCaptureInstalled = true;

  const MIN_W = 300;
  const MIN_H = 150;

  /** The element that actually scrolls. Readers often scroll an inner div. */
  function findScroller() {
    const doc = document.scrollingElement || document.documentElement;
    if (doc && doc.scrollHeight > doc.clientHeight + 100) return doc;

    let best = null;
    let bestArea = 0;
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight <= el.clientHeight + 100) continue;
      if (el.clientHeight < 200 || el.clientWidth < 200) continue;
      const oy = getComputedStyle(el).overflowY;
      if (!/auto|scroll|overlay/.test(oy)) continue;
      const area = el.clientWidth * el.clientHeight;
      if (area > bestArea) { bestArea = area; best = el; }
    }
    return best || doc;
  }

  /** Panels, in document order, largest coherent group only. */
  function findPanels() {
    const nodes = [...document.querySelectorAll('canvas, img')];
    const scored = [];
    for (const el of nodes) {
      const r = el.getBoundingClientRect();
      if (r.width < MIN_W || r.height < MIN_H) continue;
      const isImg = el.tagName === 'IMG';
      if (isImg && (!el.currentSrc && !el.src)) continue;
      scored.push({
        el,
        kind: el.tagName.toLowerCase(),
        url: isImg ? (el.currentSrc || el.src) : '',
        w: Math.round(r.width),
        h: Math.round(r.height),
      });
    }
    if (!scored.length) return [];

    // Keep only the dominant width bucket: chapter pages share a width, while
    // ads, avatars and UI art do not.
    const widths = scored.map((s) => s.w).sort((a, b) => a - b);
    const median = widths[Math.floor(widths.length / 2)];
    const kept = scored.filter((s) => Math.abs(s.w - median) <= median * 0.25);
    return (kept.length >= 2 ? kept : scored);
  }

  let panels = [];
  let scroller = null;

  // ---- paged readers -------------------------------------------------
  // Some readers hold only the current spread in the DOM. Scanning once
  // therefore sees 2 panels, not 41, and every later scan renumbers from 1.
  // So we read the pager, walk it, and keep counting across spreads.

  // Readers name this control very differently: bilibili uses "btn-right" and
  // "pagedown" and never the word "next", so a next-only pattern finds nothing.
  const NEXT_PATTERNS =
    /next|next-page|下一|pagedown|page-down|btn-right|arrow-right|forward|→|›|＞/i;

  //: The reader may hide its controls entirely; they are not in the DOM to be
  //: found until the operator (or we) ask for them back.
  //: Deliberately narrow. A looser pattern also matches `icon-open` /
  //: `icon-close`, which belong to an unrelated panel -- clicking those leaves
  //: the toolbar untouched while appearing to succeed.
  const OPENER_PATTERNS = /显示工具栏|ui-opener|show-?toolbar/i;

  /**
   * Is the reader's toolbar expanded?
   *
   * Detected from the page slider's width: collapsed, it is still in the DOM
   * but lays out at zero.
   */
  function toolbarVisible() {
    const s = [...document.querySelectorAll('input[type=range]')]
      .find((i) => Number(i.max || 0) >= 2);
    return !!(s && s.getBoundingClientRect().width > 0);
  }

  /**
   * Expand or collapse the toolbar.
   *
   * This matters for more than tidiness: bilibili DIMS the artwork while the
   * toolbar is open, so anything captured with it showing comes out at about
   * 60% brightness and white bubbles never reach white. Navigation needs the
   * toolbar; capture needs it gone.
   */
  async function setToolbar(want, settle = 450) {
    for (let i = 0; i < 4; i++) {
      if (toolbarVisible() === want) return true;
      const toggle = [...document.querySelectorAll(
        'button,[role="button"],[class*="opener"],[class*="toolbar"]')]
        .filter((el) => {
          const hay = [String(el.className || ''), el.textContent || ''].join(' ');
          if (!OPENER_PATTERNS.test(hay)) return false;
          const r = el.getBoundingClientRect();
          return r.width >= 8 && r.height >= 8;
        })
        // Prefer the button that names itself, over any container matching on
        // class alone.
        .sort((a, bb) => {
          const score = (el) => (/显示工具栏/.test(el.textContent || '') ? 2 : 0)
            + (/ui-opener/.test(String(el.className || '')) ? 1 : 0);
          return score(bb) - score(a);
        })[0];
      if (!toggle) return toolbarVisible() === want;
      toggle.click();
      await sleep(settle);
    }
    return toolbarVisible() === want;
  }

  /** Bring a hidden toolbar back, so the pager controls exist to be clicked. */
  function revealToolbar() {
    for (const el of document.querySelectorAll(
      'button,[role="button"],[class*="opener"],[class*="toolbar"]')) {
      const hay = [String(el.className || ''), el.textContent || ''].join(' ');
      if (!OPENER_PATTERNS.test(hay)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) continue;
      el.click();
      return true;
    }
    return false;
  }

  /** "2 / 21" -> {current, total}.
   *
   * A reader shows several "N / M" widgets -- a comment counter, a thumbnail
   * strip -- so taking the first match reads a different element run to run and
   * makes movement detection meaningless. The chapter pager is the one with the
   * largest total.
   */
  function readPager() {
    const found = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walk.nextNode())) {
      const t = n.nodeValue && n.nodeValue.trim();
      if (!t || t.length > 12) continue;
      const m = /^(\d+)\s*\/\s*(\d+)$/.exec(t);
      if (m) found.push({ current: +m[1], total: +m[2] });
    }
    if (!found.length) return null;
    found.sort((a, b) => b.total - a.total);
    // A total of 1 carries no information; treat it as no pager at all.
    return found[0].total > 1 ? found[0] : null;
  }

  /**
   * The reader's page slider, when it exposes one.
   *
   * Far better than clicking a "next" arrow: it addresses a view directly, so
   * the walk cannot overshoot into the following chapter. On bilibili the
   * arrow at the end of the toolbar is chapter navigation, and clicking it
   * repeatedly silently marches through chapters 1..5.
   */
  function findSlider() {
    for (const el of document.querySelectorAll('input[type=range]')) {
      const r = el.getBoundingClientRect();
      if (r.width < 40) continue;
      const max = Number(el.max || 0);
      if (!(max >= 2)) continue;
      return el;
    }
    return null;
  }

  /**
   * Move a framework-controlled input.
   *
   * Assigning `.value` directly is swallowed by React and Vue, which cache the
   * previous value on the node; the native setter has to be invoked explicitly
   * before the input event will be believed.
   */
  function setRange(input, value) {
    const proto = Object.getPrototypeOf(input);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')
      || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    if (setter && setter.set) {
      setter.set.call(input, String(value));
    } else {
      input.value = String(value);
    }
    for (const type of ['input', 'change']) {
      input.dispatchEvent(new Event(type, { bubbles: true }));
    }
  }

  /** A clickable "next" control, if the reader has one. */
  function findNext() {
    const cands = [...document.querySelectorAll(
      'button,a,[role="button"],[class*="next"],[class*="right"],' +
      '[class*="pagedown"],[class*="forward"],svg,i,span')];
    const hits = [];
    for (const el of cands) {
      const hay = [el.getAttribute('aria-label'), el.getAttribute('title'),
                   el.className && String(el.className), el.textContent]
        .filter(Boolean).join(' ');
      if (!NEXT_PATTERNS.test(hay)) continue;
      const r = el.getBoundingClientRect();
      // Real pager controls are small, visible and on screen.
      if (r.width < 8 || r.height < 8 || r.width > 200 || r.height > 200) continue;
      if (r.bottom < 0 || r.top > window.innerHeight) continue;
      // A hidden toolbar leaves its buttons in the DOM. Clicking one of those
      // silently does nothing, so findNext keeps "succeeding" while the walk
      // never moves -- which is exactly how this failed before.
      const st = getComputedStyle(el);
      if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) {
        continue;
      }
      hits.push({ el, r, score: /pagedown|btn-right|next/i.test(hay) ? 2 : 1 });
    }
    if (!hits.length) return null;
    // Prefer an explicit paging class; break ties by taking the lowest one,
    // since pager controls sit with the toolbar rather than in page chrome.
    hits.sort((a, b) => (b.score - a.score) || (b.r.top - a.r.top));
    return hits[0].el;
  }

  /** Press a key on every target a reader might be listening on. */
  function pressKey(key) {
    const init = { key, code: key, keyCode: key === 'ArrowRight' ? 39 : 34,
                   which: key === 'ArrowRight' ? 39 : 34, bubbles: true, cancelable: true };
    for (const target of [document, document.body, window,
                          document.activeElement].filter(Boolean)) {
      try {
        target.dispatchEvent(new KeyboardEvent('keydown', init));
        target.dispatchEvent(new KeyboardEvent('keyup', init));
      } catch { /* some targets reject synthetic events */ }
    }
  }

  /** How many page images the reader has fetched so far. */
  function imageCount() {
    let n = 0;
    for (const urls of Object.values(findResourceImages())) n += urls.length;
    return n;
  }

  /**
   * Did the reader actually move on?
   *
   * The pager text is not a reliable signal: bilibili's toolbar auto-hides and
   * takes the "2 / 21" counter out of the DOM with it, so it reads null most of
   * the time. New images arriving is both more robust and closer to what we
   * actually want to know.
   */
  async function advanced(beforePager, beforeImages, settle, rounds = 10) {
    for (let i = 0; i < rounds; i++) {
      await sleep(Math.max(150, settle / 2));
      const now = readPager();
      if (now && (!beforePager || now.current !== beforePager.current)) return now;
      if (imageCount() > beforeImages) return now || { current: 0, total: 0 };
    }
    return null;
  }

  /**
   * Advance one view. Tries each mechanism in turn rather than assuming one:
   * a reader may expose a button, listen only on window, or do both.
   */
  async function advance(settle) {
    const beforePager = readPager();
    const beforeImages = imageCount();
    const beforeChapter = chapterKey();

    // Some "next" controls move to the next CHAPTER once the current one ends.
    // Pages from a different chapter silently contaminate the output, so the
    // walk stops the moment the chapter identity changes.
    const sameChapter = () => chapterKey() === beforeChapter;

    // Reveal every time, not just when the control is missing: the toolbar
    // hides itself again, and a hidden button is still in the DOM -- findNext
    // keeps succeeding while the click quietly does nothing.
    await setToolbar(true);

    // Prefer the slider: it addresses a view, so it cannot leave the chapter.
    const slider = findSlider();
    if (slider) {
      const at = Number(slider.value || 0);
      const max = Number(slider.max || 0);
      if (at >= max) return null;            // genuinely the last view
      setRange(slider, at + 1);
      const moved = await advanced(beforePager, beforeImages, settle, 8);
      if (!sameChapter()) return { leftChapter: true };
      if (moved || Number(slider.value) > at) {
        return moved || { current: at + 1, total: max };
      }
    }

    const next = findNext();
    if (next) {
      next.click();
      const moved = await advanced(beforePager, beforeImages, settle, 8);
      if (!sameChapter()) return { leftChapter: true };
      if (moved) return moved;
    }

    for (const key of ['ArrowRight', 'PageDown']) {
      pressKey(key);
      const moved = await advanced(beforePager, beforeImages, settle, 5);
      if (!sameChapter()) return { leftChapter: true };
      if (moved) return moved;
    }

    // Single-image readers usually page on the wheel, which is how a person
    // moves through them. Send it where the artwork is, not at the document.
    const target = (panels[0] && panels[0].el) || document.body;
    for (const deltaY of [120, 240]) {
      target.dispatchEvent(new WheelEvent('wheel', {
        deltaY, deltaMode: 0, bubbles: true, cancelable: true,
      }));
      const moved = await advanced(beforePager, beforeImages, settle, 5);
      if (!sameChapter()) return { leftChapter: true };
      if (moved) return moved;
    }

    if (scroller) {
      scroller.scrollTop += scroller.clientHeight;
      const moved = await advanced(beforePager, beforeImages, settle, 5);
      if (!sameChapter()) return { leftChapter: true };
      if (moved) return moved;
    }
    return null;
  }

  /**
   * Image URLs the page actually fetched, from the Resource Timing buffer.
   *
   * A reader can paint pages onto a canvas while still downloading them as
   * ordinary images. Those originals are usually far larger than the on-screen
   * canvas -- measured on bilibili: 1100px wide sources displayed at 589px --
   * so re-fetching them from cache beats photographing the screen.
   *
   * This finds nothing useful where the payloads are not really images; the
   * worker verifies one decodes before trusting the mode.
   */
  function findResourceImages(minBytes = 20000) {
    let entries = [];
    try {
      entries = performance.getEntriesByType('resource') || [];
    } catch { return []; }

    const out = [];
    for (const e of entries) {
      const size = e.decodedBodySize || e.transferSize || 0;
      if (/^blob:|^data:/.test(e.name)) continue;

      // Exclude code and fonts explicitly. Getting this wrong is not harmless:
      // a 537 KB JS bundle is not an image but is also not "small", so a filter
      // written as (!isImage && size < min) lets it straight through, and the
      // host holding the most script files then wins the ranking below.
      if (/\.(js|mjs|css|wasm|json|woff2?|ttf|otf|eot|svg|mp4|m4s|ts)(\?|$)/i.test(e.name)) {
        continue;
      }

      // A raster image: by URL, or because the browser loaded it as one. The
      // pages may arrive via XHR rather than <img>, so initiatorType alone is
      // not enough -- bilibili requests them as xmlhttprequest.
      const byUrl = /\.(jpe?g|png|webp|avif|gif|bmp)(@[^?]*)?(\?|$)/i.test(e.name);
      const byInit = e.initiatorType === 'img';
      if (!byUrl && !byInit) continue;

      // Cross-origin responses without Timing-Allow-Origin report size 0, so a
      // zero here means "unknown", not "empty", and must not disqualify.
      if (size && size < minBytes) continue;

      out.push({ url: e.name, size, at: e.startTime });
    }

    // Load order approximates reading order; the reader requests pages in turn.
    out.sort((a, b) => a.at - b.at);

    // Group by host and hand every group to the worker. Ranking here is
    // guesswork: an avatar host and the page CDN look identical in the timing
    // buffer, and cross-origin entries report size 0 so bytes cannot separate
    // them either. Only decoding can, and only the worker can fetch.
    const hosts = {};
    for (const r of out) {
      let h = '';
      try { h = new URL(r.url).hostname; } catch { continue; }
      (hosts[h] = hosts[h] || []).push(r.url);
    }
    return hosts;
  }

  function scrollerInfo() {
    const isDoc = scroller === (document.scrollingElement || document.documentElement);
    return {
      isDoc,
      tag: scroller ? scroller.tagName.toLowerCase() : 'none',
      cls: scroller && scroller.className ? String(scroller.className).slice(0, 60) : '',
      scrollHeight: scroller ? scroller.scrollHeight : 0,
      clientHeight: scroller ? scroller.clientHeight : 0,
    };
  }

  /** Scroll top-to-bottom to force a virtualised reader to load everything. */
  async function primeAll(stepDelay = 350) {
    scroller = findScroller();
    const step = Math.max(200, Math.floor(scroller.clientHeight * 0.8));
    let last = -1;
    scroller.scrollTop = 0;
    await sleep(stepDelay);
    for (let i = 0; i < 400; i++) {
      scroller.scrollTop = scroller.scrollTop + step;
      await sleep(stepDelay);
      const atBottom =
        scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 40;
      if (atBottom && scroller.scrollHeight === last) break;
      last = scroller.scrollHeight;
    }
    scroller.scrollTop = 0;
    await sleep(stepDelay);
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /** Identity of the chapter currently open, so a walk cannot drift out of it. */
  function chapterKey() {
    const crumb = document.body.innerText.match(/第\s*\d+\s*[话話回]/);
    return `${location.pathname}|${crumb ? crumb[0] : ''}`;
  }

  //: How many zoom steps we applied, so they can be undone afterwards.
  let zoomApplied = 0;

  function zoomButton(kind) {
    const want = kind === 'in' ? /btn-plus|zoom-in|zoomin/i : /btn-minus|zoom-out|zoomout/i;
    for (const el of document.querySelectorAll('[class*="zoom"],button,[role="button"]')) {
      if (!want.test(String(el.className || ''))) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) continue;
      return el;
    }
    return null;
  }

  function widestPanel() {
    let w = 0, total = 0;
    for (const el of document.querySelectorAll('canvas, img')) {
      const r = el.getBoundingClientRect();
      if (r.width < 150 || r.height < 100) continue;
      w = Math.max(w, r.width);
      total += r.width;
    }
    return { w, total };
  }

  /**
   * Use the reader's own zoom to capture more detail.
   *
   * Screen capture is limited by how large the reader draws the page: bilibili
   * lays a page out at 471px against a 1100px source, which puts dialogue text
   * near the floor of what OCR can read. Zooming costs nothing and is the
   * site's own control.
   *
   * Stops before the panels exceed the viewport width -- capture slices
   * vertically only, so anything hanging off the side would be lost.
   */
  async function zoomIn(maxSteps, settle = 500) {
    zoomApplied = 0;
    const button = zoomButton('in');
    if (!button) return { applied: 0, reason: 'no zoom control' };
    for (let i = 0; i < maxSteps; i++) {
      const before = widestPanel();
      button.click();
      await sleep(settle);
      const after = widestPanel();
      if (after.total > window.innerWidth * 0.98) {
        // Too far: undo this step and stop.
        const out = zoomButton('out');
        if (out) { out.click(); await sleep(settle); }
        return { applied: zoomApplied, reason: 'would overflow the viewport' };
      }
      if (after.w <= before.w) continue;   // some steps do not change layout
      zoomApplied++;
    }
    return { applied: zoomApplied, width: widestPanel().w };
  }

  async function zoomReset(settle = 400) {
    const out = zoomButton('out');
    let undone = 0;
    while (out && zoomApplied > 0) {
      out.click();
      await sleep(settle);
      zoomApplied--; undone++;
    }
    return undone;
  }

  //: Overlays hidden for the duration of a capture, with their previous inline
  //: visibility so it can be put back exactly.
  let hidden = [];

  /**
   * Hide the reader's own furniture while a page is photographed.
   *
   * A screenshot is of the viewport, so a floating toolbar or a breadcrumb bar
   * sitting over the artwork is baked into the saved page and there is no way
   * to tell it from the comic afterwards. Page content sits in normal flow;
   * chrome is positioned fixed or sticky, which separates the two without
   * needing to know anything about the site.
   */
  function hideOverlays() {
    hidden = [];
    const targets = panels.length ? panels.map((p) => p.el)
                                  : [...document.querySelectorAll('canvas, img')];
    const rects = targets.map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 200 && r.height > 150);
    if (!rects.length) return 0;

    const intersects = (r) => rects.some((c) => {
      const w = Math.min(r.right, c.right) - Math.max(r.left, c.left);
      const h = Math.min(r.bottom, c.bottom) - Math.max(r.top, c.top);
      // Require real overlapping area: panels of a spread touch at one edge.
      return w > 8 && h > 8;
    });

    for (const el of document.querySelectorAll('body *')) {
      if (targets.includes(el)) continue;
      // Ancestors hold the page; hiding one hides the comic itself.
      if (targets.some((t) => el.contains(t) || t.contains(el))) continue;
      const st = getComputedStyle(el);
      if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) continue;
      if (!intersects(r)) continue;
      // Anything holding artwork of its own is a page, not furniture.
      const inner = el.querySelector && el.querySelector('canvas, img');
      if (inner) {
        const ir = inner.getBoundingClientRect();
        if (ir.width > 200 && ir.height > 150) continue;
      }
      hidden.push([el, el.style.visibility]);
      el.style.visibility = 'hidden';
    }
    return hidden.length;
  }

  function showOverlays() {
    for (const [el, prev] of hidden) {
      el.style.visibility = prev || '';
    }
    const n = hidden.length;
    hidden = [];
    return n;
  }

  /**
   * Put panel `index` on screen, offset `sliceTop` CSS px into it, and report
   * where it landed. The background worker crops its screenshot to this rect.
   */
  async function positionPanel(index, sliceTop, settle) {
    const panel = panels[index];
    if (!panel) return { ok: false, error: 'no such panel' };

    // scrollIntoView with an explicit block beats scrollIntoViewIfNeeded, which
    // is allowed to do nothing when the element is already "close enough".
    panel.el.scrollIntoView({ block: 'start', inline: 'nearest' });
    await sleep(40);
    if (sliceTop > 0) {
      scroller.scrollTop = scroller.scrollTop + sliceTop;
    }
    await sleep(settle);

    const r = panel.el.getBoundingClientRect();
    return {
      ok: true,
      rect: { x: r.left, y: r.top, w: r.width, h: r.height },
      viewport: { w: window.innerWidth, h: window.innerHeight },
      dpr: window.devicePixelRatio || 1,
      kind: panel.kind,
      url: panel.url,
    };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      try {
        if (msg.type === 'scan') {
          if (msg.prime) await primeAll();
          scroller = findScroller();
          panels = findPanels();
          const imgs = panels.filter((p) => p.kind === 'img').length;
          sendResponse({
            ok: true,
            total: panels.length,
            images: imgs,
            canvases: panels.length - imgs,
            // URL mode is only possible when the pages really are images.
            mode: imgs >= Math.max(2, panels.length * 0.6) ? 'urls' : 'capture',
            urls: panels.map((p) => p.url),
            sizes: panels.map((p) => ({ w: p.w, h: p.h })),
            scroller: scrollerInfo(),
            pager: readPager(),
            hasNext: !!findNext(),
            hasSlider: !!findSlider(),
            resourceHosts: findResourceImages(),
            title: document.title,
          });
          return;
        }
        if (msg.type === 'position') {
          sendResponse(await positionPanel(msg.index, msg.sliceTop || 0, msg.settle || 250));
          return;
        }
        if (msg.type === 'prime') {
          await primeAll();
          sendResponse({ ok: true });
          return;
        }
        if (msg.type === 'resources') {
          sendResponse({ ok: true, resourceHosts: findResourceImages(msg.minBytes || 20000) });
          return;
        }
        if (msg.type === 'zoomIn') {
          sendResponse({ ok: true, ...(await zoomIn(msg.steps || 0, msg.settle || 500)) });
          return;
        }
        if (msg.type === 'zoomReset') {
          sendResponse({ ok: true, undone: await zoomReset() });
          return;
        }
        if (msg.type === 'hideUI') {
          // Collapse the toolbar first: while it is open the reader dims the
          // artwork, and a dimmed capture cannot be undone without guesswork.
          const collapsed = await setToolbar(false);
          if (!panels.length) panels = findPanels();
          sendResponse({ ok: true, hidden: hideOverlays(), collapsed });
          return;
        }
        if (msg.type === 'showUI') {
          sendResponse({ ok: true, restored: showOverlays() });
          return;
        }
        if (msg.type === 'captureOne') {
          // Manual mode: photograph whatever view is on screen right now.
          await setToolbar(false);
          panels = findPanels();
          sendResponse({ ok: true, panels: panels.length });
          return;
        }
        if (msg.type === 'pager') {
          sendResponse({ ok: true, pager: readPager(), hasNext: !!findNext() });
          return;
        }
        if (msg.type === 'advance') {
          const moved = await advance(msg.settle || 400);
          panels = findPanels();
          if (moved && moved.leftChapter) {
            sendResponse({ ok: false, leftChapter: true,
                           error: 'that control moved to another chapter' });
            return;
          }
          sendResponse({ ok: !!moved, pager: moved, panels: panels.length });
          return;
        }
        sendResponse({ ok: false, error: 'unknown message' });
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e) });
      }
    })();
    return true; // keep the channel open for the async reply
  });
})();
