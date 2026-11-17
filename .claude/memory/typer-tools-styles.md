---
name: typer-tools-styles
description: "2026-10-27 Photoshop Typer Tools panel set up with one folder per series (18) of text styles for the operator's hand typing; how to regenerate"
metadata:
  node_type: memory
  type: project
  originSessionId: 3b780871-2a78-4840-ab3a-9a3b7dc17490
  modified: 2026-10-27T06:46:09.431Z
---

Operator, 2026-10-27: they type the parts I leave (SFX, missed lines) themselves with the Typer Tools CEP panel (swirt, v1.4.9) in Photoshop, and asked for a folder per series with presets matching how I letter it.

Done: `scripts/typer_styles.py --install` -> 18 series folders x 7 styles (dialogue, caption/narration, shout, whisper, aside, phone, SFX Bangers), Photoshop PostScript names, pipeline leading, centred, no hyphenation, all-caps where the pipeline uses caps; sizes measured from my renders or estimated. Storage: %APPDATA%\Adobe\CEP\extensions\typertools\storage (backup storage.bak-20261027-134344); export typertools/TyperToolsExport.json for Settings > Import. Verified in Photoshop through the panel's own createTextLayerInSelection: every face resolved, Vietnamese accents intact.

Gotchas: an open panel keeps state in memory and overwrites storage on its next change -> reopen the panel or use Import; style prefixes are matched across ALL folders, so none are set; styles have no stroke (SFX outline = layer style). Mi Imitador and conan skipped (finished). New series: add to SERIES in the script and re-run.
