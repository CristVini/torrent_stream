import queue
import weakref
import threading
import json
import time
import os
from flask import Response
from typing import Any, Dict, List

_sse_global_queues: "weakref.WeakSet[queue.Queue]" = weakref.WeakSet()
_sse_global_queues_lock = threading.Lock()
_sse_hash_queues: Dict[str, "weakref.WeakSet[queue.Queue]"] = {}
_sse_hash_queues_lock = threading.Lock()

def _sse_format(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

def _sse_broadcast_global(event: str, data: Any) -> None:
    msg = _sse_format(event, data)
    with _sse_global_queues_lock:
        for q in list(_sse_global_queues):
            try: q.put_nowait(msg)
            except queue.Full: pass

def _sse_broadcast_hash(info_hash: str, event: str, data: Any) -> None:
    msg = _sse_format(event, data)
    with _sse_hash_queues_lock:
        qs = _sse_hash_queues.get(info_hash)
        if qs:
            for q in list(qs):
                try: q.put_nowait(msg)
                except queue.Full: pass

def _start_sse_ticker():
    if hasattr(_start_sse_ticker, "_started"): return
    _start_sse_ticker._started = True

    def _ticker():
        from core.torrent import ses, active_streams, active_streams_lock
        from config import HLS_CACHE_PATH, HLS_SEGMENT_SECS, DOWNLOAD_PATH, IS_TEMPORARY
        
        while True:
            time.sleep(2)
            # Lógica de snapshot simplificada (reutilizar do torrent_stream_sse.py)
            # ... (implementação completa do ticker aqui)
            pass

    threading.Thread(target=_ticker, daemon=True).start()
