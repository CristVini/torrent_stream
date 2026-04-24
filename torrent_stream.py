# ── IMPORTS ────────────────────────────────────────────────────────────────
import base64
import io
import libtorrent as lt
import time
import os
import sys
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import requests as http_requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import json
import re
from typing import Optional, List, Dict, Any

# ── CONFIG ─────────────────────────────────────────────────────────────────
STREMIO_ADDONS = [
    "https://torrentio.strem.fun",
    "https://mediafusion.elfhosted.com",
    "https://comet.elfhosted.com",
]

ADDON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://web.stremio.com/",
}

CONFIG_FILE = os.path.join(os.path.dirname(sys.executable), "torrent_stream_config.txt")

CONTENT_TYPES = {
    ".mp4":  "video/mp4",
    ".mkv":  "video/x-matroska",
    ".avi":  "video/x-msvideo",
    ".webm": "video/webm",
    ".mov":  "video/quicktime",
    ".m4v":  "video/mp4",
    ".ts":   "video/mp2t",
}
VIDEO_EXTS = set(CONTENT_TYPES.keys())

QUALITY_ORDER = {"4K": 0, "2160P": 0, "1080P": 1, "720P": 2, "480P": 3, "SD": 4}

# Nyaa categories
NYAA_CAT_ANIME      = 1   # Anime (all)
NYAA_CAT_ANIME_EN   = 12  # Anime - English-translated
NYAA_CAT_ANIME_RAW  = 14  # Anime - Raw (for DUB detection)

# ── TRANSCODE CONFIG ────────────────────────────────────────────────────────
# Codecs de áudio que o browser NÃO suporta nativamente → precisam de transcode
AUDIO_TRANSCODE_CODECS = {
    "eac3", "ac3", "dts", "truehd", "mlp", "flac",
    "dts-hd", "dts-x", "dolby_atmos", "thd",
}

# HLS: tamanho de cada segmento em segundos
HLS_SEGMENT_SECS = 6

# Pasta de cache de transcode (subpasta de DOWNLOAD_PATH, criada no start)
HLS_CACHE_PATH = ""

# GPU encoder detectado (preenchido na primeira chamada)
_gpu_encoder: Optional[str] = None
_gpu_lock = threading.Lock()

# ── FLASK + SESSION ─────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY  = True

active_streams: Dict[str, Any] = {}
active_streams_lock = threading.Lock()

# ── CONFIG FILE ─────────────────────────────────────────────────────────────
def load_download_path() -> str:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            p = f.read().strip()
            if os.path.exists(p):
                return p
    return os.path.join(os.environ.get("TEMP", "/tmp"), "TorrentStream")

def save_download_path(path: str) -> None:
    with open(CONFIG_FILE, "w") as f:
        f.write(path)

# ── CONFIG WINDOW ───────────────────────────────────────────────────────────
def show_config_window() -> dict:
    root = tk.Tk()
    root.title("TorrentStream – Configuração")
    root.geometry("520x370")
    root.resizable(False, False)
    root.configure(bg="#1e1e2e")

    selected_path = tk.StringVar(value=load_download_path())
    temp_var      = tk.BooleanVar(value=True)
    result        = {"start": False}

    tk.Label(root, text="🎬 TorrentStream", font=("Segoe UI", 18, "bold"),
             bg="#1e1e2e", fg="#cdd6f4").pack(pady=(20, 4))
    tk.Label(root, text="Servidor de streaming via torrent",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#a6adc8").pack()

    chk_frame = tk.Frame(root, bg="#1e1e2e")
    chk_frame.pack(anchor="w", padx=30, pady=(16, 0))
    tk.Checkbutton(
        chk_frame,
        text="Usar pasta temporária (deletar arquivos ao fechar)",
        variable=temp_var, bg="#1e1e2e", fg="#a6e3a1", selectcolor="#313244",
        activebackground="#1e1e2e", activeforeground="#a6e3a1",
        font=("Segoe UI", 9), cursor="hand2",
    ).pack()

    tk.Label(root, text="Pasta para os arquivos:",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#cdd6f4").pack(anchor="w", padx=30, pady=(12, 4))

    frame = tk.Frame(root, bg="#1e1e2e")
    frame.pack(fill="x", padx=30)

    entry = tk.Entry(frame, textvariable=selected_path, font=("Segoe UI", 9),
                     bg="#313244", fg="#cdd6f4", insertbackground="white",
                     relief="flat", bd=6)
    entry.pack(side="left", fill="x", expand=True)

    def browse():
        p = filedialog.askdirectory(title="Escolha a pasta de download")
        if p:
            selected_path.set(p)

    tk.Button(frame, text="📂", command=browse, bg="#45475a", fg="#cdd6f4",
              relief="flat", font=("Segoe UI", 10), cursor="hand2",
              padx=8).pack(side="left", padx=(6, 0))

    tk.Label(root,
             text="⚠ Modo temporário: cria subpasta '.torrentstream_temp' e deleta ao fechar.",
             font=("Segoe UI", 8), bg="#1e1e2e", fg="#6c7086", wraplength=460,
             ).pack(anchor="w", padx=30)

    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(pady=20)

    def start():
        base = selected_path.get().strip()
        if not base:
            messagebox.showwarning("Atenção", "Escolha uma pasta.")
            return
        path = os.path.join(base, ".torrentstream_temp") if temp_var.get() else base
        if not temp_var.get():
            save_download_path(path)
        os.makedirs(path, exist_ok=True)
        result.update({"start": True, "path": path, "temporary": temp_var.get()})
        root.destroy()

    def uninstall():
        if messagebox.askyesno("Desinstalar", "Deletar pasta de downloads, config e executável?"):
            dl_path = selected_path.get().strip()
            if os.path.exists(dl_path):
                shutil.rmtree(dl_path, ignore_errors=True)
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            bat = os.path.join(os.environ.get("TEMP", "/tmp"), "uninstall_ts.bat")
            exe = sys.executable
            with open(bat, "w") as f:
                f.write(f'@echo off\ntimeout /t 2 /nobreak >nul\ndel /f /q "{exe}"\ndel /f /q "%~f0"\n')
            os.startfile(bat)
            messagebox.showinfo("Pronto", "TorrentStream removido com sucesso!")
            root.destroy()
            sys.exit(0)

    tk.Button(btn_frame, text="▶  Iniciar Servidor", command=start,
              bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
              relief="flat", cursor="hand2", padx=20, pady=8).pack(side="left", padx=8)
    tk.Button(btn_frame, text="🗑  Desinstalar", command=uninstall,
              bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
              relief="flat", cursor="hand2", padx=20, pady=8).pack(side="left", padx=8)

    root.mainloop()
    return result

# ── SYSTEM TRAY ─────────────────────────────────────────────────────────────
def run_tray(download_path: str, is_temporary: bool, stop_event: threading.Event) -> None:
    try:
        import pystray
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (64, 64), color=(30, 30, 80))

        def on_open_folder(icon, item):
            os.startfile(download_path)

        def on_status(icon, item):
            torrents = ses.get_torrents()
            if not torrents:
                messagebox.showinfo("Status", "Nenhum torrent ativo.")
            else:
                lines = [
                    f"{h.status().name}\n  {h.status().progress*100:.1f}% — {h.status().download_rate/1024:.0f} KB/s"
                    for h in torrents
                ]
                messagebox.showinfo("Torrents ativos", "\n\n".join(lines))

        def on_quit(icon, item):
            icon.stop()
            cleanup_all()
            stop_event.set()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("🎬 TorrentStream", None, enabled=False),
            pystray.MenuItem(f"📁 {'Temp' if is_temporary else download_path}", on_open_folder),
            pystray.MenuItem("📊 Status", on_status),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⏹ Parar", on_quit),
        )
        pystray.Icon("TorrentStream", img, "TorrentStream", menu).run()

    except ImportError:
        print("⚠ pystray não encontrado — rodando sem system tray.")
        stop_event.wait()

# ── CLEANUP ─────────────────────────────────────────────────────────────────
def cleanup_torrent(handle, file_path: str) -> None:
    try:
        ses.remove_torrent(handle)
    except Exception:
        pass
    if IS_TEMPORARY and file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            folder = os.path.dirname(file_path)
            if os.path.isdir(folder) and not os.listdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
        except Exception as e:
            print(f"Erro ao deletar: {e}")

def cleanup_all() -> None:
    if IS_TEMPORARY and os.path.exists(DOWNLOAD_PATH):
        shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
        print(f"🗑 Pasta temporária deletada: {DOWNLOAD_PATH}")

# ── FFPROBE: TRACK INFO ─────────────────────────────────────────────────────
def get_track_info(file_path: str) -> dict:
    result: dict = {"audio_tracks": [], "subtitle_tracks": [], "ffprobe_available": False}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return result

        result["ffprobe_available"] = True
        for s in json.loads(proc.stdout).get("streams", []):
            ct    = s.get("codec_type", "")
            tags  = s.get("tags", {})
            lang  = tags.get("language", tags.get("LANGUAGE", "und"))
            title = tags.get("title",    tags.get("TITLE",    ""))
            idx   = s.get("index", 0)
            codec = s.get("codec_name", "unknown")

            if ct == "audio":
                ch = s.get("channels", 0)
                ch_label = {1: "Mono", 2: "Stereo", 6: "5.1 Surround", 8: "7.1 Surround"}.get(
                    ch, f"{ch}ch" if ch else s.get("channel_layout", "")
                )
                result["audio_tracks"].append({
                    "index":          idx,
                    "language":       lang,
                    "title":          title,
                    "codec":          codec.upper(),
                    "channels":       ch,
                    "channel_layout": s.get("channel_layout", ""),
                    "channel_label":  ch_label,
                    "sample_rate":    s.get("sample_rate", ""),
                    "bit_rate":       s.get("bit_rate", ""),
                    "is_default":     s.get("disposition", {}).get("default", 0) == 1,
                    "is_forced":      s.get("disposition", {}).get("forced",  0) == 1,
                })
            elif ct == "subtitle":
                result["subtitle_tracks"].append({
                    "index":               idx,
                    "language":            lang,
                    "title":               title,
                    "codec":               codec.upper(),
                    "is_default":          s.get("disposition", {}).get("default",           0) == 1,
                    "is_forced":           s.get("disposition", {}).get("forced",            0) == 1,
                    "is_hearing_impaired": s.get("disposition", {}).get("hearing_impaired",  0) == 1,
                })
    except FileNotFoundError:
        print("⚠ ffprobe não encontrado — instale FFmpeg e adicione ao PATH")
    except Exception as e:
        print(f"ffprobe error: {e}")

    return result

# ── RANGE-AWARE STREAMING ────────────────────────────────────────────────────
def stream_file_response(file_path: str, file_size: int, content_type: str) -> Response:
    range_header = request.headers.get("Range")

    base_headers = {
        "Accept-Ranges":                 "bytes",
        "Content-Type":                  content_type,
        "Access-Control-Allow-Origin":   "*",
        "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
    }

    if range_header:
        parts  = range_header.strip().replace("bytes=", "").split("-")
        start  = int(parts[0]) if parts[0] else 0
        end    = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        end    = min(end, file_size - 1)
        length = end - start + 1

        def gen_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    data = f.read(min(256 * 1024, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return Response(gen_range(), status=206, headers={
            **base_headers,
            "Content-Range":  f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        })

    def gen_full():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                yield chunk

    return Response(gen_full(), status=200, headers={
        **base_headers,
        "Content-Length": str(file_size),
    })

# ── TORRENT BOOTSTRAP ────────────────────────────────────────────────────────
def bootstrap_magnet(magnet: str):
    params = {"save_path": DOWNLOAD_PATH, "storage_mode": lt.storage_mode_t(2)}
    handle = lt.add_magnet_uri(ses, magnet, params)

    for _ in range(60):
        if handle.has_metadata():
            break
        time.sleep(1)
    else:
        raise RuntimeError("Timeout ao buscar metadata do torrent")

    ti    = handle.get_torrent_info()
    files = ti.files()

    best_idx, best_size = -1, -1
    for i in range(files.num_files()):
        ext  = os.path.splitext(files.file_path(i))[1].lower()
        size = files.file_size(i)
        if ext in VIDEO_EXTS and size > best_size:
            best_size, best_idx = size, i

    if best_idx == -1:
        best_idx = max(range(files.num_files()), key=lambda i: files.file_size(i))

    for i in range(files.num_files()):
        handle.file_priority(i, 0)
    handle.file_priority(best_idx, 7)

    file_path    = os.path.join(DOWNLOAD_PATH, files.file_path(best_idx))
    file_size    = files.file_size(best_idx)
    ext          = os.path.splitext(file_path)[1].lower()
    content_type = CONTENT_TYPES.get(ext, "video/mp4")
    info_hash    = str(handle.status().info_hash).lower()

    with active_streams_lock:
        active_streams[info_hash] = {
            "handle":       handle,
            "file_path":    file_path,
            "file_size":    file_size,
            "content_type": content_type,
            "track_info":   None,
        }

    print(f"▶ {os.path.basename(file_path)}  ({file_size/1024/1024:.1f} MB)  [{content_type}]  hash={info_hash}")
    return handle, info_hash, file_path, file_size, content_type

# ── HELPERS ───────────────────────────────────────────────────────────────────
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

def _sort_streams(streams: List[dict]) -> List[dict]:
    return sorted(streams, key=lambda s: QUALITY_ORDER.get(s.get("quality", "SD"), 4))

# ── NYAA ENGINE ───────────────────────────────────────────────────────────────
def _nyaa_detect_type(name: str) -> str:
    """
    Detecta se o resultado é dublado, legendado ou raw pelo nome do arquivo/grupo.
    Retorna: "DUB" | "SUB" | "RAW" | ""
    """
    n = name.lower()
    if any(x in n for x in ["dual audio", "dual-audio", "dub", "dubbed", "pt-br", "ptbr"]):
        return "DUB"
    if any(x in n for x in ["legendado", "leg.", "[pt]", "portuguese"]):
        return "DUB"  # português legendado também é DUB para fins de filtragem
    if any(x in n for x in ["sub", "subtitled", "english-translated", "horriblesubs",
                              "subsplease", "erai-raws"]):
        return "SUB"
    if any(x in n for x in ["raw", "uncensored"]):
        return "RAW"
    return "SUB"  # padrão: assume legendado

def search_nyaa(keyword: str, episode: Optional[int] = None,
                season: Optional[int] = None,
                trusted_only: bool = False) -> List[dict]:
    """
    Busca no Nyaa.si via NyaaPy.
    Constrói keyword inteligente: "Anime Name S01E01" ou "Anime Name episode 1".
    Retorna lista normalizada no mesmo formato dos streams Stremio.
    """
    try:
        from nyaapy.nyaasi.nyaa import Nyaa
    except ImportError:
        print("⚠ NyaaPy não instalado — pip install nyaapy")
        return []

    try:
        # Monta query com episódio se fornecido
        query = keyword.strip()
        if season and season > 1:
            query += f" S{season:02d}"
        if episode:
            if season and season > 1:
                query += f"E{episode:02d}"
            else:
                # Season 1: usa formato "- 01" que é padrão no Nyaa para animes
                query += f" - {episode:02d}"

        filters = 2 if trusted_only else 0  # 2 = Trusted only, 0 = sem filtro

        print(f"🔍 Nyaa search: '{query}' filters={filters}")

        # Busca em categoria Anime (inclui sub e dub)
        results = Nyaa.search(keyword=query, category=NYAA_CAT_ANIME, filters=filters)

        streams = []
        for r in results:
            name    = r.name if hasattr(r, "name") else str(r.get("name", ""))
            magnet  = r.magnet if hasattr(r, "magnet") else r.get("magnet", "")
            size    = r.size if hasattr(r, "size") else r.get("size", "")
            seeders = r.seeders if hasattr(r, "seeders") else r.get("seeders", "0")

            if not magnet:
                continue

            # Extrai info_hash do magnet
            ih_match = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
            if not ih_match:
                continue
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
                "dub_type": dub_type,   # "DUB" | "SUB" | "RAW"
                "fileIdx":  None,
            })

        # Ordena por seeders (mais seeds primeiro dentro de cada qualidade)
        streams.sort(key=lambda s: (-QUALITY_ORDER.get(s["quality"], 4), -s.get("seeders", 0)))
        print(f"🌸 Nyaa: {len(streams)} resultados para '{query}'")
        return streams

    except Exception as e:
        print(f"❗ Nyaa search error: {e}")
        return []

# ── STREMIO ADDON ENGINE ─────────────────────────────────────────────────────
def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    base_url   = addon_url.rstrip("/").replace("/manifest.json", "")
    target_url = f"{base_url}/stream/{media_type}/{media_id}.json"

    try:
        r = http_requests.get(target_url, headers=ADDON_HEADERS, timeout=12)
        if r.status_code == 200:
            try:
                streams = r.json().get("streams", [])
                for s in streams:
                    s["_source"] = addon_url
                return streams
            except ValueError:
                print(f"❌ JSON inválido de {addon_url}")
        else:
            print(f"⚠ {addon_url} retornou {r.status_code}")
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

# ── ID RESOLVERS ─────────────────────────────────────────────────────────────
def resolve_imdb_id(anime_name: str) -> Optional[str]:
    try:
        url = f"https://v3-cinemeta.strem.io/catalog/series/top/search={http_requests.utils.quote(anime_name)}.json"
        r = http_requests.get(url, timeout=8)
        if r.status_code == 200:
            metas = r.json().get("metas", [])
            if metas:
                iid = metas[0].get("id")
                print(f"✅ IMDB ID: {iid} para '{anime_name}'")
                return iid
    except Exception as e:
        print(f"Cinemeta resolve error: {e}")
    return None

def resolve_kitsu_id(anime_name: str) -> Optional[str]:
    try:
        r = http_requests.get(
            "https://kitsu.io/api/edge/anime",
            params={"filter[text]": anime_name, "page[limit]": 1},
            headers={"Accept": "application/vnd.api+json"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                kid = data[0]["id"]
                print(f"✅ Kitsu ID: {kid} para '{anime_name}'")
                return kid
    except Exception as e:
        print(f"Kitsu resolve error: {e}")
    return None

def build_stremio_ids(imdb_id: Optional[str], kitsu_id: Optional[str],
                      season: int, episode: int) -> List[str]:
    """
    Monta todos os IDs no formato Stremio.
    Inclui fallback Season 1 pois muitos animes ficam indexados assim nos addons.
    """
    ids = []
    if imdb_id:
        ids.append(f"{imdb_id}:{season}:{episode}")
        if season > 1:
            ids.append(f"{imdb_id}:1:{episode}")
    if kitsu_id:
        ids.append(f"kitsu:{kitsu_id}:{season}:{episode}")
        if season > 1:
            ids.append(f"kitsu:{kitsu_id}:1:{episode}")
    return ids

# ── COMBINED SEARCH ────────────────────────────────────────────────────────────
def search_all_sources(
    name: str,
    season: int,
    episode: int,
    imdb_id: Optional[str],
    kitsu_id: Optional[str],
    use_nyaa: bool = True,
    nyaa_trusted: bool = False,
) -> List[dict]:
    """
    Busca em paralelo: Stremio addons (Torrentio, Comet, etc.) + Nyaa.si.
    Deduplica por infoHash e ordena por qualidade.
    """
    all_streams: List[dict] = []
    futures_map = {}

    with ThreadPoolExecutor(max_workers=16) as ex:

        # ── Stremio addons ──────────────────────────────────────────────────
        if imdb_id or kitsu_id:
            ids_to_try = build_stremio_ids(imdb_id, kitsu_id, season, episode)
            for addon in STREMIO_ADDONS:
                for mid in ids_to_try:
                    fut = ex.submit(_fetch_addon_streams, addon, "series", mid)
                    futures_map[fut] = ("stremio", addon)

        # ── Nyaa.si ─────────────────────────────────────────────────────────
        if use_nyaa and name:
            fut = ex.submit(search_nyaa, name, episode, season, nyaa_trusted)
            futures_map[fut] = ("nyaa", "nyaa.si")

        for future in as_completed(futures_map):
            source_type, source_name = futures_map[future]
            try:
                batch = future.result()
                if source_type == "stremio":
                    all_streams.extend([_normalize_stremio_stream(s) for s in batch])
                else:
                    all_streams.extend(batch)  # Nyaa já vem normalizado
            except Exception as e:
                print(f"❗ Erro em {source_name}: {e}")

    unique = _deduplicate(all_streams)
    sorted_streams = _sort_streams(unique)
    print(f"📦 Total final: {len(sorted_streams)} streams únicos")
    return sorted_streams


# ── GPU DETECTION ────────────────────────────────────────────────────────────
def detect_gpu_encoder() -> str:
    """
    Testa NVENC (NVIDIA) e QSV (Intel) em ordem.
    Retorna o encoder disponível ou "libx264" (CPU) como fallback.
    Resultado é cacheado em _gpu_encoder.
    """
    global _gpu_encoder
    with _gpu_lock:
        if _gpu_encoder is not None:
            return _gpu_encoder

        test_cmd_base = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "nullsrc=s=64x64:d=1",
        ]

        for encoder, label in [("h264_nvenc", "NVIDIA NVENC"), ("h264_qsv", "Intel QSV")]:
            try:
                proc = subprocess.run(
                    test_cmd_base + ["-c:v", encoder, "-f", "null", "-"],
                    capture_output=True, timeout=8,
                )
                if proc.returncode == 0:
                    print(f"✅ GPU encoder: {label} ({encoder})")
                    _gpu_encoder = encoder
                    return _gpu_encoder
            except Exception:
                pass

        print("⚠ GPU não detectada — usando libx264 (CPU)")
        _gpu_encoder = "libx264"
        return _gpu_encoder


# ── HLS TRANSCODE ENGINE ──────────────────────────────────────────────────────
def _hls_cache_dir(info_hash: str, mode: str) -> str:
    """Retorna (e cria) o diretório de cache HLS para um dado hash e modo."""
    d = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}")
    os.makedirs(d, exist_ok=True)
    return d


def _is_hls_ready(cache_dir: str) -> bool:
    """Verifica se já existe um .m3u8 válido no cache."""
    m3u8 = os.path.join(cache_dir, "index.m3u8")
    return os.path.exists(m3u8) and os.path.getsize(m3u8) > 0


def _probe_first_audio_codec(file_path: str) -> str:
    """Retorna o codec_name da primeira trilha de áudio (lower). Ex: 'eac3', 'aac'."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "a:0", file_path],
            capture_output=True, text=True, timeout=10,
        )
        streams = json.loads(proc.stdout).get("streams", [])
        if streams:
            return streams[0].get("codec_name", "").lower()
    except Exception:
        pass
    return ""


def _probe_first_video_codec(file_path: str) -> str:
    """Retorna o codec_name da primeira trilha de vídeo (lower). Ex: 'hevc', 'h264'."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", file_path],
            capture_output=True, text=True, timeout=10,
        )
        streams = json.loads(proc.stdout).get("streams", [])
        if streams:
            return streams[0].get("codec_name", "").lower()
    except Exception:
        pass
    return ""


def _needs_audio_transcode(file_path: str) -> bool:
    codec = _probe_first_audio_codec(file_path)
    return codec in AUDIO_TRANSCODE_CODECS


def _needs_video_transcode(file_path: str) -> bool:
    codec = _probe_first_video_codec(file_path)
    # HEVC/H.265, AV1, VP9 não são suportados em todos os browsers
    return codec in {"hevc", "av1", "vp9", "mpeg2video", "mpeg4"}


def start_hls_transcode(info_hash: str, file_path: str, mode: str) -> str:
    """
    Inicia o FFmpeg em background para transcodificar o arquivo em HLS.

    Modos:
      "audio"  — vídeo em copy, áudio → AAC 2.0 192k  (leve, resolve DDP/DTS)
      "full"   — vídeo → H.264 (GPU ou CPU), áudio → AAC 2.0 192k  (pesado)
      "copy"   — vídeo e áudio em copy, apenas remux para HLS/TS  (ultra-leve)

    Retorna o caminho do arquivo .m3u8 gerado.
    """
    cache_dir = _hls_cache_dir(info_hash, mode)
    m3u8_path = os.path.join(cache_dir, "index.m3u8")

    # Se já existe cache válido, retorna imediatamente
    if _is_hls_ready(cache_dir):
        print(f"♻ HLS cache hit: {cache_dir}")
        return m3u8_path

    encoder = detect_gpu_encoder()

    # ── Parâmetros de vídeo ──────────────────────────────────────────────────
    if mode == "copy" or mode == "audio":
        video_args = ["-c:v", "copy"]
    else:
        # full transcode: H.264 via GPU ou CPU
        if encoder == "h264_nvenc":
            video_args = [
                "-c:v", "h264_nvenc",
                "-preset", "p4",          # balanced speed/quality
                "-rc", "vbr",
                "-cq", "23",
                "-b:v", "0",
                "-profile:v", "high",
            ]
        elif encoder == "h264_qsv":
            video_args = [
                "-c:v", "h264_qsv",
                "-global_quality", "23",
                "-look_ahead", "1",
                "-profile:v", "high",
            ]
        else:
            # CPU libx264 — mais lento mas universal
            video_args = [
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-profile:v", "high",
                "-level", "4.1",
            ]

    # ── Parâmetros de áudio ──────────────────────────────────────────────────
    # Detecta codec atual — se já for AAC e modo != full, copia sem recodificar
    audio_codec = _probe_first_audio_codec(file_path)
    if mode == "copy":
        audio_args = ["-c:a", "copy"]
    elif audio_codec == "aac" and mode == "audio":
        audio_args = ["-c:a", "copy"]
    else:
        # DDP (EAC3) / DTS / AC3 / TrueHD → AAC Stereo 192k
        # -ac 2 garante downmix para stereo (compatível com todos os browsers)
        audio_args = [
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",          # stereo downmix — elimina DDP 5.1 incompatível
            "-ar", "48000",
        ]

    # ── Monta comando FFmpeg completo ────────────────────────────────────────
    segment_path = os.path.join(cache_dir, "seg%05d.ts")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-i", file_path,
        *video_args,
        *audio_args,
        "-sn",                            # sem legenda no stream (servida separado)
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECS),
        "-hls_list_size", "0",            # mantém todos os segmentos no m3u8
        "-hls_flags", "independent_segments+append_list",
        "-hls_segment_type", "mpegts",
        "-hls_segment_filename", segment_path,
        "-start_number", "0",
        m3u8_path,
    ]

    print(f"🎬 FFmpeg HLS [{mode}] → {cache_dir}")
    print(f"   encoder={encoder}  audio={audio_codec}→{'aac' if mode != 'copy' else 'copy'}")

    # Roda em background — Flask serve os segmentos conforme são criados
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # Registra o processo para poder matar depois
    with active_streams_lock:
        if info_hash in active_streams:
            active_streams[info_hash].setdefault("ffmpeg_procs", []).append(proc)

    # Thread que lê stderr e imprime warnings relevantes
    def _log_ffmpeg(p, label):
        for line in p.stderr:
            decoded = line.decode(errors="replace").strip()
            if decoded and "frame=" not in decoded:
                print(f"[ffmpeg/{label}] {decoded}")

    threading.Thread(target=_log_ffmpeg, args=(proc, mode[:4]), daemon=True).start()

    # Aguarda o primeiro segmento aparecer (até 30s)
    seg0 = os.path.join(cache_dir, "seg00000.ts")
    for _ in range(60):
        if os.path.exists(seg0) and os.path.getsize(seg0) > 0:
            break
        time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("FFmpeg não gerou o primeiro segmento HLS em 30s")

    return m3u8_path


def extract_subtitle_vtt(info_hash: str, file_path: str, stream_index: int = 0) -> Optional[str]:
    """
    Extrai uma trilha de legenda do MKV e converte para WebVTT.
    Retorna o caminho do .vtt gerado ou None se falhar.

    stream_index: índice da trilha de legenda (0 = primeira)
    """
    cache_dir = _hls_cache_dir(info_hash, "subs")
    vtt_path  = os.path.join(cache_dir, f"sub_{stream_index}.vtt")

    if os.path.exists(vtt_path) and os.path.getsize(vtt_path) > 0:
        return vtt_path

    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", file_path,
            "-map", f"0:s:{stream_index}",   # seleciona trilha de legenda
            "-c:s", "webvtt",                  # converte para WebVTT
            "-f", "webvtt",
            vtt_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode == 0 and os.path.exists(vtt_path):
            print(f"💬 Legenda VTT extraída: {vtt_path}")
            return vtt_path
        else:
            err = proc.stderr.decode(errors="replace")
            print(f"⚠ Falha ao extrair legenda {stream_index}: {err[:200]}")
    except Exception as e:
        print(f"❗ extract_subtitle_vtt error: {e}")

    return None


def kill_ffmpeg_procs(info_hash: str) -> None:
    """Para todos os processos FFmpeg associados a um info_hash."""
    with active_streams_lock:
        entry = active_streams.get(info_hash, {})
        procs = entry.get("ffmpeg_procs", [])

    for proc in procs:
        try:
            proc.kill()
        except Exception:
            pass

    with active_streams_lock:
        if info_hash in active_streams:
            active_streams[info_hash]["ffmpeg_procs"] = []


# ── AUTO-MODE: escolhe o modo de transcode mais leve necessário ───────────────
def auto_transcode_mode(file_path: str) -> str:
    """
    Determina automaticamente o modo de transcode necessário:
      - "copy"  → áudio e vídeo já compatíveis com browser
      - "audio" → só o áudio precisa ser convertido (DDP/DTS → AAC)
      - "full"  → vídeo HEVC/AV1 + áudio incompatível
    """
    needs_v = _needs_video_transcode(file_path)
    needs_a = _needs_audio_transcode(file_path)

    if needs_v:
        return "full"
    if needs_a:
        return "audio"
    return "copy"


# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    return jsonify({"status": "online", "version": "3.0.0"})


# ── /addons/search ────────────────────────────────────────────────────────────
@app.route("/addons/search")
def addon_search():
    """
    Busca streams por nome + temporada + episódio.
    Consulta Stremio addons E Nyaa.si em paralelo.

    Query params:
      name         — nome do anime (ex: "One Piece")
      season       — temporada (default: 1)
      episode      — episódio (default: 1)
      imdb_id      — (opcional) IMDB ID ex: tt0388629
      kitsu_id     — (opcional) Kitsu ID ex: 12189
      nyaa         — "true" | "false" (default: true) — incluir Nyaa.si
      nyaa_trusted — "true" | "false" (default: false) — somente trusted no Nyaa

    Resposta:
      {
        "total": 18,
        "streams": [
          {
            "title":    "[SubsPlease] One Piece - 1001 (1080p)",
            "infoHash": "abc123...",
            "magnet":   "magnet:?xt=urn:btih:abc123",
            "source":   "nyaa.si" | "https://torrentio.strem.fun",
            "quality":  "1080P" | "720P" | "SD",
            "size":     "317.2 MiB",
            "seeders":  538,
            "dub_type": "SUB" | "DUB" | "RAW",
            "fileIdx":  null
          }
        ],
        "meta": { name, imdb_id, kitsu_id, season, episode }
      }
    """
    name         = request.args.get("name", "").strip()
    season       = int(request.args.get("season", 1))
    episode      = int(request.args.get("episode", 1))
    imdb_id      = request.args.get("imdb_id",  "").strip() or None
    kitsu_id     = request.args.get("kitsu_id", "").strip() or None
    use_nyaa     = request.args.get("nyaa", "true").lower() != "false"
    nyaa_trusted = request.args.get("nyaa_trusted", "false").lower() == "true"

    if not name and not imdb_id and not kitsu_id:
        return jsonify({"error": "Forneça 'name', 'imdb_id' ou 'kitsu_id'"}), 400

    # Resolve IDs em paralelo se não fornecidos
    if name and (not imdb_id or not kitsu_id):
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_imdb  = ex.submit(resolve_imdb_id,  name) if not imdb_id  else None
            fut_kitsu = ex.submit(resolve_kitsu_id, name) if not kitsu_id else None
            if fut_imdb:
                imdb_id  = fut_imdb.result()
            if fut_kitsu:
                kitsu_id = fut_kitsu.result()

    streams = search_all_sources(
        name=name,
        season=season,
        episode=episode,
        imdb_id=imdb_id,
        kitsu_id=kitsu_id,
        use_nyaa=use_nyaa,
        nyaa_trusted=nyaa_trusted,
    )

    return jsonify({
        "total":   len(streams),
        "streams": streams,
        "meta": {
            "name":     name,
            "imdb_id":  imdb_id,
            "kitsu_id": kitsu_id,
            "season":   season,
            "episode":  episode,
        },
    })


# ── /nyaa/search ─────────────────────────────────────────────────────────────
@app.route("/nyaa/search")
def nyaa_search_route():
    """
    Busca direta no Nyaa.si, sem Stremio addons.
    Útil para buscar por nome exato, group release, etc.

    Query params:
      q            — keyword livre (ex: "SubsPlease One Piece 1080p")
      episode      — número do episódio (opcional)
      season       — temporada (opcional)
      trusted      — "true" | "false" (default: false)

    Resposta: mesma estrutura de /addons/search
    """
    q            = request.args.get("q", "").strip()
    episode      = request.args.get("episode", type=int)
    season       = request.args.get("season",  type=int)
    trusted      = request.args.get("trusted", "false").lower() == "true"

    if not q:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400

    streams = search_nyaa(q, episode=episode, season=season, trusted_only=trusted)
    return jsonify({"total": len(streams), "streams": streams})


# ── /addons/start ─────────────────────────────────────────────────────────────
@app.route("/addons/start", methods=["POST"])
def addon_start():
    """
    Inicia o download e devolve tudo que o player precisa em um único JSON.

    Body JSON:
      { "infoHash": "abc123", "magnet": "magnet:?xt=...", "title": "..." }

    Resposta:
      {
        "info_hash":        "abc123...",
        "stream_url":       "http://localhost:5000/stream/abc123",
        "name":             "One Piece - Episode 1001.mkv",
        "title":            "título original do stream",
        "file_size_mb":     700.4,
        "extension":        ".mkv",
        "content_type":     "video/x-matroska",
        "progress":         5.2,
        "audio_tracks":     [...],
        "subtitle_tracks":  [...],
        "ffprobe_available": true
      }
    """
    data   = request.get_json(silent=True) or {}
    ih     = data.get("infoHash", "").strip().lower()
    magnet = data.get("magnet",   "").strip()
    title  = data.get("title",    "")

    if not ih and not magnet:
        return jsonify({"error": "Forneça 'infoHash' ou 'magnet'"}), 400

    if not magnet and ih:
        magnet = f"magnet:?xt=urn:btih:{ih}"

    try:
        handle, info_hash, file_path, file_size, content_type = bootstrap_magnet(magnet)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 504

    print(f"▶ Start: {title or info_hash}")

    # Aguarda buffer mínimo (~5%) para ffprobe conseguir ler o container
    for _ in range(120):
        if handle.status().progress > 0.05:
            break
        time.sleep(1)

    # Aguarda arquivo existir no disco
    for _ in range(30):
        if os.path.exists(file_path):
            break
        time.sleep(1)

    tracks = get_track_info(file_path)

    s = handle.status()

    # Detecta automaticamente o modo de transcode necessário
    transcode_mode = auto_transcode_mode(file_path)
    print(f"🔧 Transcode mode: {transcode_mode}")

    resp = {
        "info_hash":        info_hash,
        "stream_url":       f"http://localhost:5000/stream/{info_hash}",
        "hls_url":          f"http://localhost:5000/hls/{info_hash}/index.m3u8",
        "transcode_mode":   transcode_mode,   # "copy" | "audio" | "full"
        "name":             s.name,
        "title":            title,
        "file_size_mb":     round(file_size / 1024 / 1024, 1),
        "extension":        os.path.splitext(file_path)[1].lower(),
        "content_type":     content_type,
        "progress":         round(s.progress * 100, 1),
        **tracks,
    }

    with active_streams_lock:
        if info_hash in active_streams:
            active_streams[info_hash]["track_info"] = resp

    return jsonify(resp)


# ── /stream/<hash> ────────────────────────────────────────────────────────────
@app.route("/stream/<info_hash>")
def stream_by_hash(info_hash):
    """
    Range-ready stream direto do arquivo (sem transcode).
    Use quando o browser já suporta o codec (H.264 + AAC).
    """
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404
    if not os.path.exists(entry["file_path"]):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503
    return stream_file_response(entry["file_path"], entry["file_size"], entry["content_type"])


# ── /hls/<hash>/index.m3u8 + segmentos ───────────────────────────────────────
@app.route("/hls/<info_hash>/index.m3u8")
def hls_playlist(info_hash):
    """
    Inicia (ou retorna do cache) o HLS transcoding e serve o .m3u8.

    Query params:
      ?mode=auto   (padrão) — detecta automaticamente o modo necessário
      ?mode=audio  — converte só o áudio (DDP/DTS → AAC), vídeo em copy
      ?mode=full   — converte vídeo (HEVC→H.264) + áudio
      ?mode=copy   — remux puro, sem recodificar nada

    O React deve usar esta URL no <video src> quando transcode_mode != "copy"
    ou quando o arquivo tiver codec incompatível.
    """
    info_hash = info_hash.lower()
    mode      = request.args.get("mode", "auto").lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503

    # Resolve modo automático
    if mode == "auto":
        mode = auto_transcode_mode(file_path)

    try:
        m3u8_path = start_hls_transcode(info_hash, file_path, mode)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    # Serve o .m3u8 com CORS
    with open(m3u8_path, "r") as f:
        m3u8_content = f.read()

    # Reescreve URLs dos segmentos para apontar para nossa rota
    # (FFmpeg gera caminhos locais; precisamos expor via HTTP)
    m3u8_content = re.sub(
        r"(seg\d+\.ts)",
        lambda m: f"/hls/{info_hash}/{m.group(1)}",
        m3u8_content,
    )

    return Response(
        m3u8_content,
        mimetype="application/vnd.apple.mpegurl",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
    )


@app.route("/hls/<info_hash>/<segment>")
def hls_segment(info_hash, segment):
    """
    Serve um segmento .ts do HLS.
    O browser/player requisita automaticamente conforme avança no vídeo.
    """
    info_hash = info_hash.lower()

    # Valida nome do segmento (apenas letras, números, underscore, ponto)
    if not re.match(r"^seg\d+\.ts$", segment):
        return jsonify({"error": "Segmento inválido"}), 400

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    # Procura o segmento em qualquer subpasta de cache deste hash
    cache_base = os.path.join(HLS_CACHE_PATH, "")
    seg_path   = None
    for mode in ("audio", "full", "copy"):
        candidate = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}", segment)
        if os.path.exists(candidate):
            seg_path = candidate
            break

    if not seg_path:
        # Segmento ainda sendo gerado — aguarda até 10s
        for _ in range(20):
            for mode in ("audio", "full", "copy"):
                candidate = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}", segment)
                if os.path.exists(candidate):
                    seg_path = candidate
                    break
            if seg_path:
                break
            time.sleep(0.5)

    if not seg_path:
        return jsonify({"error": "Segmento não disponível"}), 503

    file_size = os.path.getsize(seg_path)
    return stream_file_response(seg_path, file_size, "video/mp2t")


# ── /subtitles/<hash>/<index>.vtt ────────────────────────────────────────────
@app.route("/subtitles/<info_hash>/<int:sub_index>.vtt")
def serve_subtitle(info_hash, sub_index):
    """
    Extrai e serve uma trilha de legenda como WebVTT.

    Uso no React:
      <track kind="subtitles" src="http://localhost:5000/subtitles/{hash}/0.vtt" />

    sub_index: índice da trilha de legenda (0 = primeira, 1 = segunda...)
    """
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503

    vtt_path = extract_subtitle_vtt(info_hash, file_path, sub_index)
    if not vtt_path:
        return jsonify({"error": f"Legenda {sub_index} não encontrada ou erro na extração"}), 404

    with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
        vtt_content = f.read()

    return Response(
        vtt_content,
        mimetype="text/vtt",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "max-age=3600",
        },
    )


# ── /transcode/status/<hash> ──────────────────────────────────────────────────
@app.route("/transcode/status/<info_hash>")
def transcode_status(info_hash):
    """
    Retorna quantos segmentos HLS já foram gerados e se o FFmpeg ainda está rodando.
    O React pode usar para mostrar uma barra de progresso de transcode.
    """
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash, {})
        procs = entry.get("ffmpeg_procs", [])

    running = any(p.poll() is None for p in procs)

    # Conta segmentos em qualquer modo de cache
    segments = 0
    for mode in ("audio", "full", "copy"):
        d = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}")
        if os.path.isdir(d):
            segments = len([f for f in os.listdir(d) if f.endswith(".ts")])
            break

    return jsonify({
        "info_hash":   info_hash,
        "running":     running,
        "segments":    segments,
        "seconds_ready": segments * HLS_SEGMENT_SECS,
    })


# ── /status ───────────────────────────────────────────────────────────────────
@app.route("/status")
def status():
    result = []
    for h in ses.get_torrents():
        s  = h.status()
        ih = str(s.info_hash).lower()
        with active_streams_lock:
            entry = active_streams.get(ih, {})
        result.append({
            "name":               s.name,
            "info_hash":          ih,
            "progress":           round(s.progress * 100, 1),
            "progress_str":       f"{s.progress*100:.1f}%",
            "download_rate_kbps": round(s.download_rate / 1024, 1),
            "download_rate_str":  f"{s.download_rate/1024:.1f} KB/s",
            "upload_rate_kbps":   round(s.upload_rate / 1024, 1),
            "upload_rate_str":    f"{s.upload_rate/1024:.1f} KB/s",
            "peers":              s.num_peers,
            "state":              str(s.state),
            "stream_url":         f"/stream/{ih}" if ih in active_streams else None,
            "has_track_info":     bool(entry.get("track_info")),
        })
    return jsonify({
        "status":        "online",
        "torrents":      result,
        "download_path": DOWNLOAD_PATH,
        "temporary":     IS_TEMPORARY,
    })


# ── /stop ─────────────────────────────────────────────────────────────────────
@app.route("/stop", methods=["POST"])
def stop_torrent():
    data      = request.get_json(silent=True) or {}
    info_hash = data.get("info_hash", "").strip().lower()
    if not info_hash:
        return jsonify({"error": "info_hash é obrigatório"}), 400

    with active_streams_lock:
        entry = active_streams.pop(info_hash, None)

    if entry:
        kill_ffmpeg_procs(info_hash)
        threading.Thread(
            target=cleanup_torrent,
            args=(entry["handle"], entry["file_path"]),
            daemon=True,
        ).start()
        return jsonify({"success": True})

    for h in ses.get_torrents():
        if str(h.status().info_hash).lower() == info_hash:
            threading.Thread(target=cleanup_torrent, args=(h, ""), daemon=True).start()
            return jsonify({"success": True})

    return jsonify({"error": "Torrent não encontrado"}), 404


# ── /play (legado) ────────────────────────────────────────────────────────────
@app.route("/play")
def play():
    magnet = request.args.get("magnet", "").strip()
    if not magnet:
        return jsonify({"error": "magnet é obrigatório"}), 400
    try:
        handle, _, file_path, file_size, content_type = bootstrap_magnet(magnet)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 504

    for _ in range(120):
        if handle.status().progress > 0.05:
            break
        time.sleep(1)

    return stream_file_response(file_path, file_size, content_type)


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = show_config_window()
    if not result.get("start"):
        sys.exit(0)

    DOWNLOAD_PATH = result["path"]
    IS_TEMPORARY  = result["temporary"]

    # Cria pasta de cache de transcode HLS
    HLS_CACHE_PATH = os.path.join(DOWNLOAD_PATH, ".hls_cache")
    os.makedirs(HLS_CACHE_PATH, exist_ok=True)

    print(f"📁 {DOWNLOAD_PATH}  [{'temporário' if IS_TEMPORARY else 'permanente'}]")
    print(f"🎬 HLS cache: {HLS_CACHE_PATH}")
    print("🚀 http://0.0.0.0:5000")

    stop_event = threading.Event()

    threading.Thread(
        target=run_tray,
        args=(DOWNLOAD_PATH, IS_TEMPORARY, stop_event),
        daemon=True,
    ).start()

    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, threaded=True),
        daemon=True,
    ).start()

    stop_event.wait()
    cleanup_all()
    sys.exit(0)