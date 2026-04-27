import libtorrent as lt
import time
import os
import threading
from typing import Dict, Any, Tuple
from config import BUFFER_READY_BYTES, BUFFER_READY_TIMEOUT_S, VIDEO_EXTS, CONTENT_TYPES

ses = lt.session()
ses.listen_on(6881, 6891)

active_streams: Dict[str, Any] = {}
active_streams_lock = threading.Lock()

def bootstrap_magnet(magnet: str) -> Tuple[lt.torrent_handle, str, str, int, str]:
    params = lt.parse_magnet_uri(magnet)
    info_hash = str(params.info_hash).lower()

    with active_streams_lock:
        if info_hash in active_streams:
            entry = active_streams[info_hash]
            return entry["handle"], info_hash, entry["file_path"], entry["file_size"], entry["content_type"]

    handle = ses.add_torrent(params)
    print(f"🧲 Magnet adicionado: {info_hash[:8]}... aguardando metadados")

    for _ in range(60):
        if handle.has_metadata(): break
        time.sleep(0.5)
    else:
        raise RuntimeError("Timeout ao baixar metadados do torrent")

    ti = handle.get_torrent_info()
    video_files = []
    for i, f in enumerate(ti.files()):
        ext = os.path.splitext(f.path)[1].lower()
        if ext in VIDEO_EXTS:
            video_files.append((i, f.path, f.size, CONTENT_TYPES.get(ext, "video/mp4")))

    if not video_files:
        raise RuntimeError("Nenhum arquivo de vídeo encontrado no torrent")

    video_files.sort(key=lambda x: x[2], reverse=True)
    idx, rel_path, file_size, content_type = video_files[0]

    from config import DOWNLOAD_PATH
    file_path = os.path.join(DOWNLOAD_PATH, rel_path)

    handle.set_sequential_download(True)
    for i in range(ti.num_files()):
        handle.file_priority(i, 7 if i == idx else 0)

    with active_streams_lock:
        active_streams[info_hash] = {
            "handle": handle,
            "file_path": file_path,
            "file_size": file_size,
            "content_type": content_type,
            "ffmpeg_procs": [],
        }

    return handle, info_hash, file_path, file_size, content_type

def wait_for_buffer(file_path: str) -> bool:
    for _ in range(int(BUFFER_READY_TIMEOUT_S * 2)):
        if os.path.exists(file_path) and os.path.getsize(file_path) >= BUFFER_READY_BYTES:
            return True
        time.sleep(0.5)
    return False
