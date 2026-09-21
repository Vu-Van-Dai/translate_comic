# Comic Chapter Capture

A Chrome extension that saves a chapter's pages from the reader you are already
viewing, into a folder `comic import-raw` can ingest.

It exists because automated acquisition fails on several readers for reasons that
are not worth fighting:

- the reader scrolls an inner container, so page-level scrolling does nothing;
- the host serves an anti-bot interstitial to automated browsers;
- the pages are painted onto `<canvas>` rather than served as images.

You are already past all three when you are simply reading the chapter. This
extension works from there.

## Install

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. **Load unpacked** → select this `extension/` folder

## Use

1. Open the chapter and let it load.
2. Click the extension icon.
3. Fill in **Series** and **Chapter**, then **Scan this page**.
   The scan scrolls the whole chapter (finding the real scroll container first),
   then reports how many panels it found and which mode applies.
4. Set **Views to walk** and **Start numbering at**.

   Paged readers (bilibili is one) hold only the *current* view in the page --
   two canvases, not the whole chapter. So the control is how many times to
   press "next", not a range within one view. The scan reads the reader's own
   counter ("2 / 21") and pre-fills the number of views remaining, so the
   default takes the rest of the chapter.

   **Start numbering at** continues an interrupted run. After a run finishes it
   advances automatically, so a second run carries on from where the first
   stopped instead of overwriting `001.png`.
5. **Capture range**. Leave the tab in the foreground and do not switch windows:
   screen capture photographs the *visible* tab.
6. Run the command shown at the bottom of the popup:

```
comic import-raw --series "Paws & Claws" --chapter 69
```

`import-raw` finds the folder itself, shows you the exact rename mapping for
confirmation, and only then writes into the workspace.

## The three modes

The scan picks the best available automatically, in this order.

**image URLs** — the pages are real `<img>` elements. Their URLs are fetched
with your session and saved directly. Lossless.

**original images** — the pages are painted onto `<canvas>`, but the reader
still downloaded them as ordinary images. The Resource Timing buffer says what
was fetched, and those originals are re-fetched from cache. Also lossless, and
usually much larger than what is on screen: measured on bilibili, sources are
1100px wide against a 589px canvas.

The worker fetches a sample and checks it decodes as a page-sized image before
trusting this mode. Where a reader's payloads are transformed rather than
served as images, the check fails and screen capture is used instead.

**screen capture** — nothing else is available. **Prefer
`comic capture --chrome` instead**: measured on bilibili, this path returns
pages dimmed to 60% (peak luminance 153 against 255 for the same canvas
captured over CDP), and the cause was never found. Use this mode only where
CDP is not an option. Each panel is photographed with
`chrome.tabs.captureVisibleTab`, and panels taller than the viewport are
captured in slices and stitched.

Output is at screen resolution. That matters: a 589px-wide manga page puts
dialogue text at 10-25px tall, and Chinese or Japanese OCR wants 20-32px. If
you are stuck in this mode, zoom the reader in before capturing.

The popup reports **image sources seen** after every scan. If that number is 0
while the mode is screen capture, the reader either genuinely does not fetch
images, or the timing buffer was already full — reload the page and scan again
before capturing the chapter.

## What it does not do

It does not call `toDataURL()` on a page's canvas and does not touch any
decoding the page performs. Where a site protects its content, that protection
is left intact — screen capture is the same thing you can do by hand, which is
the point.

Pages are de-duplicated by content hash, so re-visiting a view you already
captured costs nothing and cannot produce a duplicate page. Downloads never
overwrite: a name collision is uniquified rather than silently replacing an
earlier run's work.

It also skips blank captures rather than saving them: a virtualised reader
returns unpainted placeholders for panels that never rendered, and a chapter of
those would otherwise look like a successful run. The popup reports the blank
count so a bad run is obvious.

## The reader's own UI

In screen-capture mode the extension hides anything overlapping a page --
toolbars, breadcrumbs, comment/danmaku layers -- for the moment of capture, and
restores it afterwards. Without that, the furniture is baked into the saved page.

Overlays are found by geometry, not by CSS position: readers commonly lay their
chrome out as absolutely-positioned siblings of the page, so a
`position: fixed` rule finds nothing.

This does not arise in **original images** mode, which takes the files the
reader downloaded and therefore never contains UI.

## If a capture comes out blank

The reader had not painted that panel yet. Scroll through the chapter once by
hand so everything renders, then scan and capture again.
