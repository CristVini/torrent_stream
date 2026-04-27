import requests as http_requests
from typing import List, Optional
from config import ADDON_HEADERS, STREMIO_ADDONS
from utils import _extract_quality, _extract_size
from engines.nyaa import _nyaa_detect_type

def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    base_url   = addon_url.rstrip("/").replace("/manifest.json", "")
    target_url = f"{base_url}/stream/{media_type}/{media_id}.json"
    try:
        r = http_requests.get(target_url, headers=ADDON_HEADERS, timeout=12)
        if r.status_code == 200:
            streams = r.json().get("streams", [])
            for s in streams: s["_source"] = addon_url
            return streams
    except Exception as e:
        print(f"❗ Falha em {addon_url}: {e}")
    return []

def _normalize_stremio_stream(s: dict) -> dict:
    title = s.get("title", "") or ""
    return {
        "title":    title,
        "infoHash": s.get("infoHash"),
        "magnet":   f"magnet:?xt=urn:btih:{s['infoHash']}" if s.get("infoHash") else None,
        "source":   s.get("_source", ""),
        "fileIdx":  s.get("fileIdx"),
        "quality":  _extract_quality(title),
        "size":     _extract_size(title),
        "seeders":  0,
        "dub_type": _nyaa_detect_type(title),
    }

def resolve_imdb_id(anime_name: str) -> Optional[str]:
    try:
        url = f"https://v3-cinemeta.strem.io/catalog/series/top/search={http_requests.utils.quote(anime_name)}.json"
        r = http_requests.get(url, timeout=8)
        if r.status_code == 200:
            metas = r.json().get("metas", [])
            if metas: return metas[0].get("id")
    except Exception: pass
    return None

def resolve_kitsu_id(anime_name: str) -> Optional[str]:
    try:
        r = http_requests.get("https://kitsu.io/api/edge/anime",
            params={"filter[text]": anime_name, "page[limit]": 1},
            headers={"Accept": "application/vnd.api+json"}, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data: return data[0]["id"]
    except Exception: pass
    return None
