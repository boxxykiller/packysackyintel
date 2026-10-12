"""Self-update from this project's GitHub repository.

Flow: read latest.json (version, size, SHA-256) from the repo's main branch -> if that version is newer than
VERSION, download the exe from the repo (checking size and SHA-256) to a temp folder -> start a tiny batch script
that waits for this process to exit, swaps the exe in place and relaunches it. Only ever talks to
raw.githubusercontent.com for the repository named in version.py, over HTTPS."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from version import ASSET_NAME, REPO, VERSION

# PACKY_UPDATE_BASE only exists so the updater can be tested against a local server.
BASE = os.environ.get("PACKY_UPDATE_BASE") or f"https://raw.githubusercontent.com/{REPO}/main/"
HEADERS = {"User-Agent": "PackySackyIntelTool-updater"}


def parse_version(text):
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def check_latest(timeout=8):
    """Return {'version', 'url', 'size', 'sha256'} if a newer version is published, else None.
    Any network / permission problem (offline, private repo, ...) just raises and the caller carries on."""
    req = urllib.request.Request(BASE + "latest.json", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        meta = json.load(resp)
    if parse_version(meta.get("version")) <= parse_version(VERSION):
        return None
    return {"version": str(meta["version"]).lstrip("vV"), "url": BASE + urllib.parse.quote(ASSET_NAME),
            "size": int(meta.get("size") or 0), "sha256": (meta.get("sha256") or "").lower()}


def download(info, progress=None, timeout=30):
    """Download the new exe to a temp folder and return its path. Raises on a size / hash mismatch."""
    if not info["url"].startswith(BASE):
        raise ValueError("unexpected download location")
    if not info.get("sha256"):
        raise ValueError("latest.json has no checksum - refusing to install an unverified exe")
    folder = tempfile.mkdtemp(prefix="packy_update_")
    path = os.path.join(folder, "new.exe")
    sha = hashlib.sha256()
    req = urllib.request.Request(info["url"], headers=HEADERS)
    done = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(path, "wb") as out:
        total = int(resp.headers.get("Content-Length") or info.get("size") or 0)
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    if info.get("size") and done != info["size"]:
        raise ValueError(f"download is {done} bytes, expected {info['size']}")
    if sha.hexdigest() != info["sha256"]:
        raise ValueError("download failed its SHA-256 check")
    with open(path, "rb") as fh:
        if fh.read(2) != b"MZ":
            raise ValueError("download is not a Windows executable")
    return path


def apply_and_restart(new_exe, current_exe=None):
    """Start a detached script that replaces current_exe with new_exe once this process has exited, then
    relaunches it with --updated (so it doesn't check again straight away). The caller must exit right after."""
    current_exe = current_exe or sys.executable
    bat = os.path.join(os.path.dirname(new_exe), "apply_update.bat")
    script = "\r\n".join([
        "@echo off",
        "set tries=0",
        ":retry",
        "set /a tries+=1",
        "ping 127.0.0.1 -n 2 >nul",
        f'move /Y "{new_exe}" "{current_exe}" >nul 2>&1',
        "if errorlevel 1 (",
        "  if %tries% LSS 40 goto retry",
        "  exit /b 1",
        ")",
        f'start "" "{current_exe}" --updated',
        'del "%~f0"',
    ])
    with open(bat, "w") as fh:
        fh.write(script + "\r\n")
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000 | 0x00000008, close_fds=True)   # no window, detached
