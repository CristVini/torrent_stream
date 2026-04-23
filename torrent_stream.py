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
    resp = {
        "info_hash":        info_hash,
        "stream_url":       f"http://localhost:5000/stream/{info_hash}",
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
    Range-ready stream. Use diretamente como <video src>.
    Suporta seeking em MKV via HTTP 206 Partial Content.
    """
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404
    if not os.path.exists(entry["file_path"]):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503
    return stream_file_response(entry["file_path"], entry["file_size"], entry["content_type"])


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
    print(f"📁 {DOWNLOAD_PATH}  [{'temporário' if IS_TEMPORARY else 'permanente'}]")
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