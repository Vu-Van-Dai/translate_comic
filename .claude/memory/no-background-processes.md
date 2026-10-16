---
name: no-background-processes
description: Nothing from this project may auto-start at boot or keep running after work; shut down every related process when done
metadata:
  type: feedback
---

No project component (comic pipeline workers, debugging Chrome/Edge from `comic chrome`, Photoshop launched over COM, Playwright browsers, models) may register to start with Windows or be left running after a job. Never add Run keys, Startup-folder shortcuts, scheduled tasks or services for it. When a job finishes, close what Claude launched (debug browsers on 9222/9223, Photoshop if Claude started it, stray python workers).

Audited 2026-10-12: startup holds only Docker Desktop, Edge autolaunch, Realtek audio, SecurityHealth, iGoAudio, Lightshot (Skillbrains) updater — none ours.

**Why:** user's explicit instruction (2026-10-12); render workers pinning the CPU made the machine lag.
**How to apply:** check `Get-CimInstance Win32_Process` for leftovers at the end of each work session. See [[work-autonomously]].
