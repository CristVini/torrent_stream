import re
from typing import List, Optional
from config import NYAA_CAT_ANIME, QUALITY_ORDER
from utils import _extract_quality

def _nyaa_detect_type(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["dual audio", "dual-audio", "dub", "dubbed", "pt-br", "ptbr"]):
        return "DUB"
    if any(x in n for x in ["legendado", "leg.", "[pt]", "portuguese"]):
        return "DUB"
    if any(x in n for x in ["sub", "subtitled", "english-translated", "horriblesubs",
                              "subsplease", "erai-raws"]):
        return "SUB"
    if any(x in n for x in ["raw", "uncensored"]):
        return "RAW"
    return "SUB"

def search_nyaa(keyword: str, episode: Optional[int] = None,
                season: Optional[int] = None,
                trusted_only: bool = False) -> List[dict]:
    try:
        from nyaapy.nyaasi.nyaa import Nyaa
    except ImportError:
        print("⚠ NyaaPy não instalado — pip install nyaapy")
        return []

    try:
        query = keyword.strip()
        if season and season > 1:
            query += f" S{season:02d}"
        if episode:
            if season and season > 1:
                query += f"E{episode:02d}"
            else:
                query += f" - {episode:02d}"

        filters = 2 if trusted_only else 0
        print(f"🔍 Nyaa search: '{query}' filters={filters}")

        results = Nyaa.search(keyword=query, category=NYAA_CAT_ANIME, filters=filters)

        streams = []
        for r in results:
            name    = r.name if hasattr(r, "name") else str(r.get("name", ""))
            magnet  = r.magnet if hasattr(r, "magnet") else r.get("magnet", "")
            size    = r.size if hasattr(r, "size") else r.get("size", "")
            seeders = r.seeders if hasattr(r, "seeders") else r.get("seeders", "0")

            if not magnet: continue

            ih_match = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
            if not ih_match: continue
            ih = ih_match.group(1).lower()

            quality  = _extract_quality(name)
            dub_type = _nyaa_detect_type(name)

            streams.append({
                "title":    name,
                "infoHash": ih,
                "magnet":   magnet,
                "source":   "nyaa.si",
                "quality":  quality,
                "size":     size,
                "seeders":  int(seeders) if str(seeders).isdigit() else 0,
                "dub_type": dub_type,
                "fileIdx":  None,
            })

        streams.sort(key=lambda s: (-QUALITY_ORDER.get(s["quality"], 4), -s.get("seeders", 0)))
        return streams
    except Exception as e:
        print(f"❗ Nyaa search error: {e}")
        return []
