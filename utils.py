import subprocess
import sys
import os
import re
import json
from typing import List, Optional

def _win_startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags    |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si

def _run(cmd: List[str], timeout: int = 15, text: bool = True) -> subprocess.CompletedProcess:
    kwargs: dict = {"capture_output": True, "timeout": timeout}
    if text:
        kwargs["text"]     = True
        kwargs["encoding"] = "utf-8"
        kwargs["errors"]   = "replace"
    si = _win_startupinfo()
    if si:
        try:
            return subprocess.run(cmd, **kwargs, startupinfo=si)
        except OSError as e:
            if hasattr(e, "winerror") and e.winerror == 6:
                print(f"⚠ _run WinError 6 com STARTUPINFO, fallback: {cmd[0]}")
            else:
                raise
    return subprocess.run(cmd, **kwargs)

def _popen(cmd: List[str]) -> subprocess.Popen:
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
    }
    si = _win_startupinfo()
    if si:
        try:
            return subprocess.Popen(cmd, **kwargs, startupinfo=si)
        except OSError as e:
            if hasattr(e, "winerror") and e.winerror == 6:
                print(f"⚠ _popen WinError 6 com STARTUPINFO, fallback: {cmd[0]}")
            else:
                raise
    try:
        return subprocess.Popen(cmd, **kwargs)
    except OSError:
        print(f"⚠ _popen PIPE falhou, abrindo sem stderr: {cmd[0]}")
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _extract_quality(title: str) -> str:
    m = re.search(r"(4K|2160p|1080p|720p|480p)", title or "", re.IGNORECASE)
    return m.group(1).upper() if m else "SD"

def _extract_size(title: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB|MiB|GiB))", title or "", re.IGNORECASE)
    return m.group(1) if m else ""

def _deduplicate(streams: List[dict]) -> List[dict]:
    seen, unique = set(), []
    for s in streams:
        ih = s.get("infoHash")
        if ih and ih not in seen:
            seen.add(ih)
            unique.append(s)
    return unique
