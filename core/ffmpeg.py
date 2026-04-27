import os
import threading
import json
import time
import shutil
from typing import List, Optional, Dict
from utils import _run, _popen
from config import HLS_SEGMENT_SECS, AUDIO_TRANSCODE_CODECS

_hls_start_locks: Dict[str, threading.Lock] = {}
_hls_start_locks_meta = threading.Lock()

def _get_hls_lock(info_hash: str) -> threading.Lock:
    with _hls_start_locks_meta:
        if info_hash not in _hls_start_locks:
            _hls_start_locks[info_hash] = threading.Lock()
        return _hls_start_locks[info_hash]

def detect_gpu_encoder() -> str:
    try:
        r = _run(["ffmpeg", "-encoders"], timeout=5)
        if "h264_nvenc" in r.stdout: return "h264_nvenc"
        if "h264_qsv" in r.stdout: return "h264_qsv"
    except Exception: pass
    return "libx264"

def _probe_streams(file_path: str) -> dict:
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path]
        r = _run(cmd, timeout=15)
        data = json.loads(r.stdout)
        v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
        a = next((s for s in data["streams"] if s["codec_type"] == "audio"), {})
        return {
            "video_codec": v.get("codec_name", "unknown"),
            "audio_codec": a.get("codec_name", "unknown"),
            "audio_channels": int(a.get("channels", 2)),
        }
    except Exception:
        return {"video_codec": "unknown", "audio_codec": "unknown", "audio_channels": 2}

def start_hls_transcode(info_hash: str, file_path: str, mode: str) -> str:
    from config import HLS_CACHE_PATH
    hls_lock = _get_hls_lock(info_hash)
    cache_dir = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}")
    os.makedirs(cache_dir, exist_ok=True)
    m3u8_path = os.path.join(cache_dir, "index.m3u8")

    with hls_lock:
        if os.path.exists(m3u8_path) and os.path.getsize(m3u8_path) > 0:
            return m3u8_path

        info = _probe_streams(file_path)
        encoder = detect_gpu_encoder()
        
        # Simplificado para o exemplo, manter a lógica original de args aqui
        video_args = ["-c:v", "copy"] if mode in ("copy", "audio") else ["-c:v", encoder]
        audio_args = ["-c:a", "copy"] if mode == "copy" else ["-c:a", "aac"]

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-i", file_path, *video_args, *audio_args,
            "-f", "hls", "-hls_time", str(HLS_SEGMENT_SECS),
            "-hls_list_size", "0", m3u8_path
        ]
        
        proc = _popen(cmd)
        from core.torrent import active_streams, active_streams_lock
        with active_streams_lock:
            if info_hash in active_streams:
                active_streams[info_hash].setdefault("ffmpeg_procs", []).append(proc)
        
        return m3u8_path

def kill_ffmpeg_procs(info_hash: str):
    from core.torrent import active_streams, active_streams_lock
    with active_streams_lock:
        if info_hash in active_streams:
            for p in active_streams[info_hash].get("ffmpeg_procs", []):
                try: p.kill()
                except Exception: pass
            active_streams[info_hash]["ffmpeg_procs"] = []
