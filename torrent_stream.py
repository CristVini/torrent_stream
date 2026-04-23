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
from concurrent.futures import ThreadPoolExecutor
import subprocess  # Adicione esta linha
import json

# ── CONFIG ─────────────────────────────────────────────────────────────────
STREMIO_ADDONS = [
    "https://torrentio.strem.fun",
    "https://mediafusion.elfhosted.com",
    "https://comet.elfhosted.com",
]

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

# ── FLASK + SESSION ─────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY  = True

# info_hash (lower) -> {handle, file_path, file_size, content_type, track_info}
active_streams      = {}
active_streams_lock = threading.Lock()

# ── CONFIG FILE ─────────────────────────────────────────────────────────────
def load_download_path():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            p = f.read().strip()
            if os.path.exists(p):
                return p
    return os.path.join(os.environ.get("TEMP", "/tmp"), "TorrentStream")

def save_download_path(path):
    with open(CONFIG_FILE, "w") as f:
        f.write(path)

# ── CONFIG WINDOW ───────────────────────────────────────────────────────────
def show_config_window():
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
def run_tray(download_path, is_temporary, stop_event):
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
def cleanup_torrent(handle, file_path):
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

def cleanup_all():
    if IS_TEMPORARY and os.path.exists(DOWNLOAD_PATH):
        shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
        print(f"🗑 Pasta temporária deletada: {DOWNLOAD_PATH}")

# ── FFPROBE: TRACK INFO ─────────────────────────────────────────────────────
def get_track_info(file_path):
    import subprocess
    import json

    result = {"audio_tracks": [], "subtitle_tracks": [], "ffprobe_available": False}
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
                ch_label = {
                    1: "Mono", 2: "Stereo", 6: "5.1 Surround", 8: "7.1 Surround"
                }.get(ch, f"{ch}ch" if ch else s.get("channel_layout", ""))
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
                    "is_default":     s.get("disposition", {}).get("default",          0) == 1,
                    "is_forced":      s.get("disposition", {}).get("forced",           0) == 1,
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
        pass  # ffprobe not installed
    except Exception as e:
        print(f"ffprobe error: {e}")

    return result

# ── RANGE-AWARE STREAMING ────────────────────────────────────────────────────
def stream_file_response(file_path, file_size, content_type):
    """Returns a Flask Response with proper Range / 206 support for MKV seeking."""
    range_header = request.headers.get("Range")

    base_headers = {
        "Accept-Ranges":                "bytes",
        "Content-Type":                 content_type,
        "Access-Control-Allow-Origin":  "*",
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
    """
    Adds a magnet URI, waits for metadata, selects the largest video file,
    registers in active_streams, returns (handle, info_hash, file_path, file_size, content_type).
    Raises RuntimeError on timeout.
    """
    params = {"save_path": DOWNLOAD_PATH, "storage_mode": lt.storage_mode_t(2)}
    handle = lt.add_magnet_uri(ses, magnet, params)

    for _ in range(60):
        if handle.has_metadata():
            break
        time.sleep(1)
    else:
        raise RuntimeError("Timeout ao buscar metadata")

    ti    = handle.get_torrent_info()
    files = ti.files()

    # Prefer largest video file; fall back to absolute largest
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

# ── STREMIO ADDON ENGINE ─────────────────────────────────────────────────────
def _fetch_addon_streams(addon_url, media_type, media_id):
    # 1. Limpeza rigorosa da URL base
    base_url = addon_url.replace("/manifest.json", "").rstrip("/")
    target_url = f"{base_url}/stream/{media_type}/{media_id}.json"
    
    # 2. Headers idênticos ao teste (O SEGREDO DO SUCESSO)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://web.stremio.com/"
    }

    try:
        # Usamos http_requests ou requests normal, dependendo de como importou
        r = http_requests.get(target_url, headers=headers, timeout=12)
        
        if r.status_code == 200:
            # Tratamento de JSON seguro para evitar o erro "line 1 column 1"
            try:
                data = r.json()
                streams = data.get("streams", [])
                for s in streams:
                    s["_source"] = addon_url
                return streams
            except ValueError:
                print(f"❌ Erro de decodificação JSON em {addon_url}")
                return []
        else:
            print(f"⚠️ Addon {addon_url} retornou status {r.status_code}")
            
    except Exception as e:
        print(f"❗ Falha de conexão com {addon_url}: {e}")
    
    return []

def get_all_addon_streams(media_type, media_id):
    all_streams = []
    # Usar a lista global de addons que você definiu (Torrentio, Comet, etc)
    with ThreadPoolExecutor(max_workers=len(STREMIO_ADDONS)) as ex:
        # O map vai rodar o _fetch_addon_streams para cada addon
        results = ex.map(lambda a: _fetch_addon_streams(a, media_type, media_id), STREMIO_ADDONS)
        
        for batch in results:
            if batch: # Só adiciona se houver resultados
                all_streams.extend(batch)

    # Lógica de remoção de duplicatas por infoHash
    seen, unique = set(), []
    for s in all_streams:
        ih = s.get("infoHash")
        if ih and ih not in seen:
            seen.add(ih)
            unique.append(s)
    
    print(f"📦 Total de streams encontrados: {len(unique)}")
    return unique

# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    return jsonify({"status": "online", "version": "2.1.0"})

# ── METADADOS E MULTI-ÁUDIO ──────────────────────────────────────────────────
@app.route("/stream/metadata")
def get_metadata():
    """
    Analisa o arquivo via FFprobe para detectar faixas de áudio e legendas.
    O Front-end usa isso para montar o seletor dinâmico.
    """
    stream_url = request.args.get("url")
    if not stream_url:
        return jsonify({"error": "URL de stream necessária"}), 400

    try:
        # Comando FFprobe para extrair apenas informações de streams (áudio e legenda)
        cmd = [
            'ffprobe', 
            '-v', 'quiet', 
            '-print_format', 'json', 
            '-show_streams', 
            '-select_streams', 'a', # Seleciona apenas áudio inicialmente para o menu
            stream_url
        ]
        
        # Executa o processo de análise (requer FFmpeg instalado no PC)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        probe_data = json.loads(result.stdout)
        
        audio_tracks = []
        for i, stream in enumerate(probe_data.get("streams", [])):
            audio_tracks.append({
                "index": stream.get("index"),
                "id": i,
                "codec": stream.get("codec_name"),
                "language": stream.get("tags", {}).get("language", "und"),
                "title": stream.get("tags", {}).get("title", f"Áudio {i+1}"),
                "channels": stream.get("channels")
            })

        return jsonify({
            "has_multi_audio": len(audio_tracks) > 1,
            "audio_tracks": audio_tracks,
            "format": probe_data.get("format", {})
        })

    except Exception as e:
        print(f"🔥 Erro no FFprobe: {e}")
        return jsonify({"error": "Falha ao analisar metadados", "details": str(e)}), 500

@app.route("/stream/config")
def stream_config():
    """
    Endpoint auxiliar para o Front-end salvar preferências de áudio/legenda
    """
    return jsonify({
        "supported_codecs": ["aac", "ac3", "mp3", "opus"],
        "dual_audio_enabled": True,
        "engine_version": "2.1.0-pro"
    })

@app.route("/play")
def play():
    """Start download + stream. Use /stream/<hash> for seeking afterwards."""
    magnet = request.args.get("magnet", "").strip()
    if not magnet:
        return jsonify({"error": "magnet é obrigatório"}), 400
    try:
        handle, _, file_path, file_size, content_type = bootstrap_magnet(magnet)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 504

    # Wait for initial buffer (~5 %)
    for _ in range(120):
        if handle.status().progress > 0.05:
            break
        time.sleep(1)

    return stream_file_response(file_path, file_size, content_type)


@app.route("/stream/<info_hash>")
def stream_by_hash(info_hash):
    """Range-ready stream endpoint — set this as your <video> src."""
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404
    if not os.path.exists(entry["file_path"]):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503
    return stream_file_response(entry["file_path"], entry["file_size"], entry["content_type"])


@app.route("/info/<info_hash>")
def track_info(info_hash):
    """Returns ffprobe track info (audio channels, subtitles, codecs)."""
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Torrent não encontrado"}), 404

    # Return cached result
    if entry.get("track_info"):
        return jsonify(entry["track_info"])

    file_path = entry["file_path"]
    handle    = entry["handle"]

    for _ in range(30):
        if os.path.exists(file_path):
            break
        time.sleep(1)
    else:
        return jsonify({"error": "Arquivo não encontrado no disco"}), 503

    # Need at least 5 % for ffprobe to read the container header
    for _ in range(60):
        if handle.status().progress >= 0.05:
            break
        time.sleep(2)

    tracks = get_track_info(file_path)
    s      = handle.status()
    result = {
        "info_hash":    info_hash,
        "name":         s.name,
        "file_path":    file_path,
        "file_size":    entry["file_size"],
        "file_size_mb": round(entry["file_size"] / 1024 / 1024, 1),
        "content_type": entry["content_type"],
        "extension":    os.path.splitext(file_path)[1].lower(),
        "stream_url":   f"/stream/{info_hash}",
        "progress":     round(s.progress * 100, 1),
        **tracks,
    }

    with active_streams_lock:
        active_streams[info_hash]["track_info"] = result

    return jsonify(result)


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
            "info_url":           f"/info/{ih}"   if ih in active_streams else None,
            "has_track_info":     bool(entry.get("track_info")),
        })
    return jsonify({
        "status":        "online",
        "torrents":      result,
        "download_path": DOWNLOAD_PATH,
        "temporary":     IS_TEMPORARY,
    })


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


# ── ID RESOLVERS ─────────────────────────────────────────────────────────────

def resolve_kitsu_id(anime_name: str) -> str | None:
    """
    Busca o ID do Kitsu pelo nome do anime usando a API pública do Kitsu.
    Retorna o ID (ex: "12189") ou None se não encontrar.
    """
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
                return data[0]["id"]
    except Exception as e:
        print(f"Kitsu resolve error: {e}")
    return None


def resolve_imdb_id(anime_name: str) -> str | None:
    """
    Busca o IMDB ID via CINEMETA (catálogo público do Stremio).
    Retorna o tt-id (ex: "tt0388629") ou None.
    """
    try:
        # Cinemeta search endpoint
        r = http_requests.get(
            f"https://v3-cinemeta.strem.io/catalog/series/top/search={http_requests.utils.quote(anime_name)}.json",
            timeout=8,
        )
        if r.status_code == 200:
            metas = r.json().get("metas", [])
            if metas:
                return metas[0].get("id")
    except Exception as e:
        print(f"Cinemeta resolve error: {e}")
    return None


def build_series_id(media_id: str, season: int, episode: int) -> str:
    """
    Monta o ID no formato esperado pelos addons Stremio para series/anime.
    - IMDB:  tt0388629:1:1
    - Kitsu: kitsu:12189:1
    """
    if media_id.startswith("tt"):
        return f"{media_id}:{season}:{episode}"
    else:
        # Kitsu usa season implícita, somente episódio global ou S:E
        return f"kitsu:{media_id}:{season}:{episode}"


# ── STREMIO ADDON ROUTES ──────────────────────────────────────────────────────

@app.route("/addons/search")
def addon_search():
    name     = request.args.get("name", "").strip()
    season   = request.args.get("season", "1")
    episode  = request.args.get("episode", "1")
    imdb_id  = request.args.get("imdb_id", "").strip()
    kitsu_id = request.args.get("kitsu_id", "").strip()

    # 1. Validação e Resolução de ID
    if not imdb_id or imdb_id == "null":
        if name:
            print(f"🔍 Resolvendo ID para: {name}")
            imdb_id = resolve_imdb_id(name)
        else:
            return jsonify({"error": "Nome ou IMDB_ID necessário"}), 400

    if not imdb_id:
        return jsonify({"total": 0, "streams": [], "error": "Não foi possível localizar o ID do anime"})

    # 2. Lógica de Fallback (Busca Generosa)
    # Tentamos o ID literal e também a Season 1 (onde muitos animes ficam guardados)
    ids_to_try = [f"{imdb_id}:{season}:{episode}"]
    if int(season) > 1:
        ids_to_try.append(f"{imdb_id}:1:{episode}")
    
    if kitsu_id:
        ids_to_try.append(f"kitsu:{kitsu_id}:1:{episode}")

    # 3. Busca em massa
    all_raw_streams = []
    for mid in ids_to_try:
        # A função get_all_addon_streams agora usa os novos HEADERS internamente
        batch = get_all_addon_streams("series", mid)
        all_raw_streams.extend(batch)

    # 4. Extração de Dados e Deduplicação (O que o seu front precisa)
    import re
    seen, streams_out = set(), []
    
    for s in all_raw_streams:
        ih = s.get("infoHash")
        if ih and ih not in seen:
            seen.add(ih)
            title = s.get("title", "")
            
            # Extração de qualidade (Regex do seu código antigo)
            q_match = re.search(r"(4K|2160p|1080p|720p|480p)", title, re.IGNORECASE)
            quality = q_match.group(1).upper() if q_match else "SD"
            
            # Extração de tamanho
            s_match = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB))", title, re.IGNORECASE)
            size = s_match.group(1) if s_match else ""

            streams_out.append({
                "title": title,
                "infoHash": ih,
                "magnet": f"magnet:?xt=urn:btih:{ih}",
                "source": s.get("_source", "Unknown"),
                "quality": quality,
                "size": size,
                "fileIdx": s.get("fileIdx")
            })

    # 5. Ordenação (Melhor qualidade primeiro)
    q_map = {"4K": 0, "2160P": 0, "1080P": 1, "720P": 2, "480P": 3, "SD": 4}
    streams_out.sort(key=lambda x: q_map.get(x["quality"], 5))

    return jsonify({
        "total": len(streams_out),
        "streams": streams_out,
        "meta": {
            "name": name,
            "imdb_id": imdb_id,
            "season": season,
            "episode": episode
        }
    })
    
@app.route("/addons/streams")
def addon_streams():
    """
    Queries all configured Stremio addons in parallel.
    ?type=movie|series  &id=tt1234567
    Returns deduplicated stream list with magnet links.
    """
    media_type = request.args.get("type", "movie")
    media_id   = request.args.get("id", "").strip()
    if not media_id:
        return jsonify({"error": "id é obrigatório"}), 400

    streams = get_all_addon_streams(media_type, media_id)
    return jsonify({
        "streams": [
            {
                "title":    s.get("title"),
                "infoHash": s.get("infoHash"),
                "magnet":   f"magnet:?xt=urn:btih:{s['infoHash']}" if s.get("infoHash") else None,
                "source":   s.get("_source"),
                "fileIdx":  s.get("fileIdx"),
            }
            for s in streams
        ]
    })


@app.route("/addons/start", methods=["POST"])
def addon_start():
    """
    Endpoint principal de reprodução — recebe a escolha do usuário e devolve
    tudo que o player precisa em um único JSON.

    Body JSON:
      {
        "infoHash": "abc123...",          -- obrigatório
        "magnet":   "magnet:?xt=...",     -- opcional (infoHash já é suficiente)
        "title":    "One Piece S01E01"    -- opcional, para exibição
      }

    Resposta (JSON):
      {
        "info_hash":   "abc123...",
        "stream_url":  "http://localhost:5000/stream/abc123",
        "name":        "One Piece - Episode 1.mkv",
        "file_size_mb": 700.4,
        "extension":   ".mkv",
        "content_type": "video/x-matroska",
        "progress":    5.2,
        "audio_tracks": [
          { "index": 0, "language": "jpn", "title": "Japanese", "codec": "AAC",
            "channels": 2, "channel_label": "Stereo", "is_default": true, "is_forced": false }
        ],
        "subtitle_tracks": [
          { "index": 1, "language": "por", "title": "Português", "codec": "ASS",
            "is_default": false, "is_forced": false, "is_hearing_impaired": false }
        ],
        "ffprobe_available": true
      }

    O React só precisa:
      1. Chamar POST /addons/start com o stream escolhido
      2. Usar stream_url direto no <video src>
      3. Exibir audio_tracks e subtitle_tracks para o usuário escolher
      4. Chamar GET /status para polling de progresso (opcional)
      5. Chamar POST /stop com info_hash ao sair
    """
    data     = request.get_json(silent=True) or {}
    ih       = data.get("infoHash", "").strip().lower()
    magnet   = data.get("magnet", "").strip()
    title    = data.get("title", "")

    if not ih and not magnet:
        return jsonify({"error": "Forneça 'infoHash' ou 'magnet'"}), 400

    # Monta magnet se só tiver hash
    if not magnet and ih:
        magnet = f"magnet:?xt=urn:btih:{ih}"

    # ── 1. Bootstrap: metadata + prioridade de arquivo ────────────────────────
    try:
        handle, info_hash, file_path, file_size, content_type = bootstrap_magnet(magnet)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 504

    print(f"▶ Start: {title or info_hash}")

    # ── 2. Aguarda buffer mínimo (~5%) para ffprobe funcionar ──────────────────
    for _ in range(120):
        if handle.status().progress > 0.05:
            break
        time.sleep(1)

    # ── 3. Aguarda arquivo existir no disco ────────────────────────────────────
    for _ in range(30):
        if os.path.exists(file_path):
            break
        time.sleep(1)

    # ── 4. Analisa trilhas via ffprobe ─────────────────────────────────────────
    tracks = get_track_info(file_path)

    # ── 5. Monta resposta completa ─────────────────────────────────────────────
    s = handle.status()
    response = {
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

    # Cacheia no active_streams
    with active_streams_lock:
        if info_hash in active_streams:
            active_streams[info_hash]["track_info"] = response

    return jsonify(response)


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