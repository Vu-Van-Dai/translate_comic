const $ = (id) => document.getElementById(id);
let scanned = null;
let tab = null;

function log(line) {
  $('log').textContent = (line + '\n' + $('log').textContent).slice(0, 4000);
}

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

/** The content script may not be injected yet on an already-open tab. */
async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  } catch (e) {
    log('inject: ' + e.message);
  }
}

function send(tabId, msg) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, msg, (r) => {
      resolve(chrome.runtime.lastError
        ? { ok: false, error: chrome.runtime.lastError.message }
        : (r || { ok: false, error: 'no response' }));
    });
  });
}

function guessChapter(url, title) {
  // URL slug, then a CJK chapter marker such as 第 001 话.
  const fromUrl = /chapter[-_/]?(\d+(?:\.\d+)?)/i.exec(url || '');
  if (fromUrl) return fromUrl[1];
  const fromTitle = /第\s*(\d+)\s*[话話回]/.exec(title || '');
  return fromTitle ? String(parseInt(fromTitle[1], 10)) : '';
}

function guessSeries(title) {
  // Titles read like "001 - Series - site". A leading number is the chapter,
  // not the series, so skip purely numeric segments.
  const parts = String(title || '').split(/[-|–—>]/).map((s) => s.trim());
  return parts.find((s) => s && !/^\d+$/.test(s) && s.length <= 40) || '';
}

function updateCommand() {
  const s = $('series').value || 'Series';
  const c = $('chapter').value || '1';
  $('cmd').textContent = `comic import-raw --series "${s}" --chapter ${c}`;
  $('next').hidden = false;
}

$('scan').addEventListener('click', async () => {
  tab = await activeTab();
  $('scan').disabled = true;
  $('scanHint').textContent = 'Scanning — this scrolls the chapter to load every page…';
  await ensureContentScript(tab.id);

  const r = await send(tab.id, { type: 'scan', prime: true });
  $('scan').disabled = false;
  if (!r.ok) {
    $('scanHint').textContent = 'Scan failed: ' + (r.error || 'unknown');
    return;
  }
  scanned = r;
  $('scanHint').textContent = 'Scanned.';
  $('found').hidden = false;
  // A paged reader exposes only the current view, so the useful control is how
  // many times to press next -- not a page range within a single view.
  const pager = r.pager;
  if (pager) {
    const remaining = Math.max(1, pager.total - pager.current + 1);
    $('spreads').value = remaining;
    $('walkHint').textContent =
      `Reader is at view ${pager.current} of ${pager.total}. ` +
      `Walking ${remaining} views x ${r.total} panels = about ` +
      `${remaining * r.total} pages.`;
  } else {
    $('spreads').value = 1;
    $('walkHint').textContent = r.hasNext
      ? 'No page counter found; set how many times to press next.'
      : 'Single view — everything is already on the page.';
  }

  // Prefer the downloaded originals when they exist and really decode:
  // measured on bilibili, they are 1100px wide against a 589px canvas.
  let note = '';
  const hostMap = r.resourceHosts || {};
  const sourceCount = Object.values(hostMap).reduce((n, v) => n + v.length, 0);
  if (sourceCount >= 1 && r.mode !== 'urls') {
    $('scanHint').textContent = 'Checking for original images…';
    let probe = await new Promise((res) =>
      chrome.runtime.sendMessage({ type: 'probe', hosts: hostMap }, res));

    // A reader may not request a single page image until you move off the first
    // view -- measured on bilibili: 0 comic URLs in the timing buffer at view 1,
    // 7 after one step. Probing only what has loaded by then always concludes
    // "no originals", so nudge it forward once and look again.
    if (!(probe && probe.usable)) {
      $('scanHint').textContent = 'Advancing one view to look for originals…';
      await send(tab.id, { type: 'advance', settle: 400 });
      await new Promise((r) => setTimeout(r, 2500));
      const again = await send(tab.id, { type: 'resources' });
      const map2 = (again && again.resourceHosts) || {};
      if (Object.keys(map2).length) {
        probe = await new Promise((res) =>
          chrome.runtime.sendMessage({ type: 'probe', hosts: map2 }, res));
      }
    }

    if (probe && probe.usable) {
      scanned.mode = 'resources';
      scanned.host = probe.host;
      note = ` — originals are ${probe.w}x${probe.h} from ${probe.host}`;
    } else {
      const tried = (probe && probe.tried || []).join(', ');
      note = ` — ${sourceCount} source(s), none page-sized` + (tried ? ` (${tried})` : '');
    }
    $('scanHint').textContent = 'Scanned.';
  }
  scanned.sourceCount = sourceCount;

  const pills = {
    urls: '<span class="pill urls">image URLs</span>',
    resources: '<span class="pill resources">original images</span>',
    capture: '<span class="pill capture">screen capture</span>',
  };
  const modePill = pills[scanned.mode] + note;
  const sc = r.scroller || {};
  $('info').innerHTML =
    `<div><b>${r.total}</b> panels — ${r.images} image, ${r.canvases} canvas &nbsp; ${modePill}</div>` +
    `<div class="k" style="margin-top:4px">scrolls: ${sc.isDoc ? 'page' : `&lt;${sc.tag}&gt; ${sc.cls}`} ` +
    `(${sc.scrollHeight}px)</div>` +
    `<div class="k">image sources seen: ${sourceCount}</div>`;

  if (!$('chapter').value) $('chapter').value = guessChapter(tab.url, r.title);
  if (!$('series').value) $('series').value = guessSeries(r.title);
  updateCommand();
});

function launch(spreads) {
  if (!scanned) return;
  $('start').disabled = true;
  $('startOne').disabled = true;
  $('cancel').hidden = false;
  $('log').textContent = '';
  updateCommand();

  const opts = {
    tabId: tab.id,
    windowId: tab.windowId,
    series: $('series').value || 'Series',
    chapter: $('chapter').value || '1',
    spreads,
    startAt: parseInt($('startAt').value, 10) || 1,
    mode: scanned.mode,
    host: scanned.host || '',
    zoom: parseInt($('zoom').value, 10) || 0,
    settle: scanned.mode === 'capture' ? 450 : 0,
  };

  chrome.runtime.sendMessage({ type: 'start', opts }, (r) => {
    $('start').disabled = false;
    $('startOne').disabled = false;
    $('cancel').hidden = true;
    if (!r) { log('no reply from worker'); return; }
    if (!r.ok) { log('failed: ' + r.error); return; }
    log(`done — ${r.saved} saved, ${r.blank} blank, ${r.failed} failed`);
    log('folder: Downloads/' + r.dir);
    if (r.nextNumber) {
      $('startAt').value = r.nextNumber;
      log(`next run will start numbering at ${r.nextNumber}`);
    }
  });
}

$('startOne').addEventListener('click', async () => {
  tab = tab || await activeTab();
  launch(1);
});

$('start').addEventListener('click', async () => {
  tab = tab || await activeTab();
  launch(parseInt($('spreads').value, 10) || 1);
});

$('cancel').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'cancel' }, () => log('cancelling…'));
});

chrome.runtime.onMessage.addListener((m) => {
  if (m.type !== 'progress') return;
  if (m.done) {
    $('bar').style.width = '100%';
    if (m.nextNumber) $('startAt').value = m.nextNumber;
    log(`${m.cancelled ? 'cancelled' : 'done'} — ${m.saved} saved, ${m.blank} blank, ${m.failed} failed`);
    return;
  }
  const pct = Math.round((m.index / (m.last || 1)) * 100);
  $('bar').style.width = pct + '%';
  log(`view ${m.index}/${m.last} — saved ${m.saved}` +
      (m.blank ? `, blank ${m.blank}` : '') +
      (m.failed ? `, failed ${m.failed}` : '') +
      (m.note ? ` — ${m.note}` : ''));
});

for (const id of ['series', 'chapter']) {
  $(id).addEventListener('input', updateCommand);
}

// Restore the last series/chapter so repeat chapters are one edit.
chrome.storage.local.get(['series', 'chapter'], (v) => {
  if (v.series) $('series').value = v.series;
  if (v.chapter) $('chapter').value = v.chapter;
});
window.addEventListener('beforeunload', () => {
  chrome.storage.local.set({ series: $('series').value, chapter: $('chapter').value });
});
