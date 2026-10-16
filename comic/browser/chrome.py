"""Attaching to the operator's own Chrome.

Some hosts serve an anti-bot interstitial to any browser that looks automated,
including a plainly-launched Chromium. This project does not try to defeat that
check. Instead it can borrow a browser the host has *already* cleared: the
operator's own Chrome, which they sign into and browse with normally.

Chrome 136 and later refuse ``--remote-debugging-port`` on the default profile,
so a dedicated profile directory is used. It is a real, persistent Chrome
profile -- sign in and pass challenges in it once and both stick.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from comic.paths import project_root

DEFAULT_PORT = 9222

#: Where the dedicated debugging profile lives. Kept out of the browser profile
#: used for launched Chromium so the two cannot fight over a lock file.
def chrome_profile_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "profiles" / "_chrome"


_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


_EDGE = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def edge_profile_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "profiles" / "_edge"


def find_edge() -> Path | None:
    """Locate Microsoft Edge -- the same Chromium CDP endpoint, a separate browser."""
    found = shutil.which("msedge")
    if found:
        return Path(found)
    for candidate in _EDGE:
        if Path(candidate).exists():
            return Path(candidate)
    return None


def find_chrome() -> Path | None:
    """Locate a Chrome/Edge binary, preferring Chrome."""
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return Path(found)
    local = os.environ.get("LOCALAPPDATA")
    candidates = list(_CANDIDATES)
    if local:
        candidates.insert(0, str(Path(local) / "Google/Chrome/Application/chrome.exe"))
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def cdp_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def is_listening(port: int = DEFAULT_PORT, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def debug_target(port: int = DEFAULT_PORT) -> dict | None:
    """Query the CDP endpoint; confirms it is really Chrome and not something else."""
    try:
        with urlopen(f"{cdp_url(port)}/json/version", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def launch(
    port: int = DEFAULT_PORT, profile: Path | None = None, edge: bool = False
) -> subprocess.Popen | None:
    """Start Chrome with remote debugging on a dedicated profile.

    Returns ``None`` if no Chrome binary could be found. Does nothing if the
    port is already serving a CDP endpoint.
    """
    if debug_target(port):
        return None

    binary = find_edge() if edge else find_chrome()
    if binary is None:
        return None

    target = profile or (edge_profile_dir() if edge else chrome_profile_dir())
    target.mkdir(parents=True, exist_ok=True)

    return subprocess.Popen(
        [
            str(binary),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={target}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def manual_command(port: int = DEFAULT_PORT, profile: Path | None = None, edge: bool = False) -> str:
    """The command to paste if automatic launching does not work."""
    binary = find_edge() if edge else find_chrome()
    target = profile or (edge_profile_dir() if edge else chrome_profile_dir())
    exe = f'"{binary}"' if binary else "chrome.exe"
    return f'{exe} --remote-debugging-port={port} --user-data-dir="{target}"'
