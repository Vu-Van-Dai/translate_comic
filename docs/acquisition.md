# Acquisition: downloading, capturing and importing chapters

Read this before downloading a chapter, capturing a reader, importing raw pages or
adding a site adapter.

Moved here from CLAUDE.md word for word (2026-10-20) so the everyday rules stay
short; CLAUDE.md links to this file. The rules below are as binding as CLAUDE.md's.

## Access-gated sites (measured 2026-09-20, yaoiscan re-measured 2026-09-21)

| Host | Plain automation | Route |
|---|---|---|
| comix.to | 200, pages load | direct, but lazy-loads — see note |
| manga.bilibili.com | 200 | needs the operator's login |
| theblank.net | 403 Cloudflare interstitial | `--chrome`, plus `comic capture` |
| yaoiscan.com | 403 headless; clears itself in a real Chrome | `--chrome`, **proven** |

A bot challenge is a **terminal outcome**, raised as `BotChallengeError`. Do not
add stealth patches, fingerprint spoofing or CAPTCHA solving to get past one —
that is circumventing an access control and is out of scope for this project.
The supported route is `comic chrome` + `comic download --chrome`, which reads
through a browser the operator signed into and the site has already cleared.

**That route is now proven, not theoretical.** On yaoiscan the interstitial
(localized: `Chờ một chút...`) cleared by itself within three seconds of a real
Chrome loading the page -- no interaction, no patching -- and `download
--chrome` then pulled a chapter: 6 files, 720px wide, each ~17000px tall,
97325px of strip in total. So the 403 in the row above is what *headless*
automation sees, not a wall.

When attached over CDP the browser is **borrowed, not owned**: `close()` must
disconnect without closing it, or it will shut the operator's windows.

Repeated automated hits get throttled. comix.to served 3 images on one visit and
an empty document shortly after. Prefer the saved fixtures in `tests/fixtures/`
over re-probing a live host.

## What theblank.net actually does (measured)

Pages are **not** images. The chapter is ~89 `<canvas>` elements; the only
`<img>` tags are the site logo. Page data arrives from
`/p/<token>/1/<key>/<n>-1080.ece` as `application/octet-stream` with **no image
magic bytes at any offset** -- it is encrypted and decoded in JS before being
painted. Every image visible on the network is an ad CDN.

So: no decryption, and no `toDataURL` on those canvases. Both defeat a content
protection measure. `comic capture` screenshots rendered elements instead.

The reader also **virtualises**: only canvases near the viewport are painted.
Screenshotting the rest returns a uniform placeholder (measured: exactly 2
distinct colours, ~6 KB, vs 3000-8000 colours for real artwork). `capture.py`
therefore waits for paint and re-checks, and counts what never painted --
otherwise a chapter of blank pages looks like a success.

Known-unfixed: `Emulation.setDeviceMetricsOverride` does not raise screenshot
resolution on a CDP-attached Chrome, so captures come back at CSS size (901px
wide) rather than the source's 1080px. Worth less than it sounds -- OCR
accuracy saturates well below that, see "Resolution saturates around 500px"
(in `docs/ocr-and-detection.md`).

## Harvesting originals from the Resource Timing buffer

A reader can paint pages onto a canvas while still downloading them as ordinary
images. `extension/content.js: findResourceImages` finds those, and they are far
better than a screenshot -- measured on bilibili: **1100x1699 AVIF originals**
against a **589x913** screen capture, i.e. ~3.5x the pixels, lossless.

Two mistakes to avoid, both made and fixed here:

- **Filter to images by exclusion, not by size.** A filter written as
  `if (!isImage && size < min) continue` keeps a 537 KB JS bundle, because it is
  not an image *and* not small. Exclude `.js/.css/.wasm/...` explicitly first.
- **Rank candidate hosts by count, not bytes.** Cross-origin responses without
  `Timing-Allow-Origin` report `decodedBodySize: 0`, so on exactly the hosts
  that matter a byte ranking sorts by zeros and picks arbitrarily.

Together these picked `s1.hdslb.com` (19 script files) over `manga.hdslb.com`
(3 real pages), so every fetched "image" failed to decode.

The page itself **cannot** fetch these (CORS); the extension's service worker
can, because it holds `host_permissions`. Always probe-and-verify before
trusting the mode: where payloads are transformed rather than served as images,
the decode check fails and screen capture is used instead, which is how the
protected case stays protected without special-casing it.

## Walking a paged reader (measured on bilibili)

A paged reader keeps only the current view in the DOM, so one scan sees 2
panels rather than the chapter. Walking it has four traps, all hit here:

- **Synthetic keyboard events do not work.** A content script cannot produce
  trusted input, and bilibili ignores dispatched `KeyboardEvent`s. Playwright's
  `keyboard.press` works only because CDP delivers real input. The walk must
  therefore click a real element.
- **The control is not called "next".** bilibili names it `btn-right` /
  `pagedown`. A pattern matching only `next|下一|›` finds nothing.
- **The toolbar auto-hides, and its buttons stay in the DOM.** `findNext` keeps
  returning an element while the click does nothing. Reveal the toolbar
  (`显示工具栏` / `[class*=opener]`) *every* time, and skip elements that are
  `visibility:hidden`, `display:none` or `opacity:0`.
- **The pager text is not a usable signal.** It lives in that same hidden
  toolbar, so it reads null most of the time, and several unrelated "N / M"
  widgets exist -- taking the first match compares different elements run to
  run. Prefer the largest total, and detect movement by *new page images
  arriving* instead.

Verified: reveal + click `SPAN.pagedown` loads 3 new `manga.hdslb.com` images
per step.

## Screen capture includes whatever floats over the page

A viewport screenshot bakes the reader's own furniture into the saved page --
bilibili's breadcrumb, its floating toolbar, and its `bullet-screen` danmaku
layer -- and afterwards nothing can separate that from the comic.

Do **not** identify chrome by `position: fixed|sticky`: measured on bilibili,
that matches zero elements, because the overlays are `absolute` siblings inside
the same container as the canvas. Use geometry instead -- hide anything that
overlaps a panel with real area (>8px both axes, since the two panels of a
spread touch at one edge) and is neither an ancestor nor a descendant of it,
skipping any element that holds artwork of its own.

Restore the inline `visibility` afterwards, and verify the artwork is unchanged:
hiding an ancestor hides the comic. The check that matters is that the middle of
the panel differs by 0 before and after.

Better still, avoid the problem: **resources mode has no UI in it at all**,
because it takes the originals the reader downloaded rather than a photograph.

## Capture through CDP, not through the extension

Measured on the same canvas, same page, same moment:

| path | peak luminance |
|---|---|
| `chrome.tabs.captureVisibleTab` (extension) | **153** |
| element screenshot over CDP (`comic capture --chrome`) | **255** |

The extension's screen-capture mode dims every page to ~60% and there was no
way to talk it out of it. `comic capture --chrome` is therefore the supported
path for canvas readers: it screenshots the panel *element*, which also means
toolbars and danmaku never appear and nothing has to be hidden.

The extension remains useful for its **image-URL** and **original-images**
modes, and for stepping a reader by hand. Do not spend more time on its
screen-capture path.

## The toolbar dims the artwork (bilibili)

Opening bilibili's toolbar (显示工具栏, class `ui-opener`) dims the page so the
controls read over it. Captures taken with it open come back at ~60% brightness
-- measured peak luminance 153 instead of 255 -- and white speech bubbles never
reach white, which is exactly the contrast OCR depends on.

This was self-inflicted: `revealToolbar()` clicked that button before every
advance. Navigation needs the toolbar; capture must not have it. So
`setToolbar(true)` before navigating, `setToolbar(false)` before capturing.

Match that toggle narrowly. A pattern including `opener` also matches
`icon-open` / `icon-close`, which belong to an unrelated panel: clicking those
appears to succeed while the toolbar never moves.

Detect toolbar state from the page slider's width -- collapsed, it stays in the
DOM at zero width.

If pages were already captured dimmed, `comic import-raw --normalize` applies a
linear stretch back to full range (153 -> 255). It is **off by default**: the
workspace copy is the verbatim archive of what the source served.

## Acquisition ladder

Try in order; each rung is independent of the one above.

1. **auto** — `comic download <url> --series X`. Headless browser, network capture.
2. **chrome** — `comic chrome`, then `comic download ... --chrome`. Reads through
   a browser the operator signed into and the host has already cleared.
3. **capture** — `comic capture <url> --series X`. Screenshots rendered panels,
   for readers that paint rather than serve their pages.
4. **manual** — the `extension/` Chrome extension, then `comic import-raw`.
   Independent of the site's structure entirely, and the only rung that always
   works. Prefer it over more site-specific debugging.

`import-raw` needs no `--from`: it checks `raw/<series>/<chapter>/` then the
extension's `~/Downloads/comic-raw/<series>/<chapter>/`.

A manual import has no adapter to inherit from, so it defaults to Japanese,
paged and right-to-left -- wrong in all three respects for a Korean webtoon,
and not inferable from the files. Pass `--language ko --kind longstrip`
(which implies `ttb`) or OCR gets the wrong engine and the strip is never
re-cut.

### Scrolling

Never assume the window scrolls. Measured on bilibili: `document.scrollHeight`
is 0 while 76 panels live inside a container, so every `window.scrollBy` is a
no-op and no pages load — silently. Find the real scroller first (largest
element with `overflowY: auto|scroll` and `scrollHeight > clientHeight`), then
set `scrollTop` on it. Prefer `scrollIntoView({block:'start'})` over
`scrollIntoViewIfNeeded`, which may legitimately do nothing.

Use `removeprefix`, not `lstrip`, to strip `"Chapter_"` — `lstrip` takes a
character set and eats the `E` off `Extra`.

## Adding a site

Site support is data, not code. Drop a YAML file in `comic/downloader/sites/`:

```yaml
match: "example.com"
page_kind: longstrip        # longstrip | paged
reading_direction: ttb      # ttb | rtl | ltr
container_selector: "#readerarea, .reading-content"
min_image_width: 600
chapter_number_regex: "chapter-(\\d+(?:\\.\\d+)?)"
requires_login: false
```

The generic importer handles lazy loading, scroll-triggered loading and expiring CDN
URLs by capturing image bytes off the network as the browser receives them. Write a
`.py` adapter only when YAML genuinely cannot express the site.
