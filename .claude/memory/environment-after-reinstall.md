---
name: environment-after-reinstall
description: "Machine was reset ~2026-10-12; where Python/Git live now and the quirks of calling them from Claude's shell"
metadata:
  node_type: memory
  type: project
  originSessionId: 46bc7c94-cc19-44c6-8827-eac8f27431cf
  modified: 2026-10-12T01:40:52.038Z
---

Windows was reinstalled around 2026-10-12 (new user SID); drive E: with the project survived intact (fonts in assets/fonts, models/, profiles/, workspace/, library/ all present). Prior Claude memory was lost with it.

- Python 3.12.10 reinstalled per-user via winget: `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. `python` on PATH in a fresh shell may still hit the WindowsApps Store stub — prepend `...\Python312` and `...\Python312\Scripts` to `$env:Path`.
- Git: `C:\Program Files\Git\cmd\git.exe`, not on PATH in Claude's shell. Repo files are owned by the old SID, so git refuses with "dubious ownership"; use `git -c safe.directory=* ...` unless the user adds a global safe.directory.
- Since 2026-10-11 memory lives in the project at `.claude/memory/`; the user-profile memory dir is a junction to it (recreate command in CLAUDE.md).
- Repo has a single commit ("Initial commit"); nearly all work is uncommitted in the working tree.
- After restore: bootstrap + `comic doctor` 24 ok / 0 blocking; pytest 333 passed, 1 skipped.
- Photoshop 2026 (v27) lives on D: (`D:\phan mem hoc\Adobe Photoshop 2026`), registered for COM. Restored 2026-10-12: all 23 project fonts installed per-user, pywin32 added as the `photoshop` extra; `comic ps check` connects, doctor 25 ok / 0 warnings.

**Why:** the user asked to recover after losing the environment; these are the non-obvious traps found.
**How to apply:** set PATH before running `comic`/pytest; don't assume a missing tool means the project is broken.
