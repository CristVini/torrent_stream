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


# ── Ícone embutido (base64) ───────────────────────────────────────────────────
ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAAfmElEQVR4nLWbeZTdR3XnP7eqfr/3Xr/u16/31tba1UKSbbyAbYhZzGrWxBCHLQRmMifDzGRIYDJkJpMzYSY5mRMIHCYnAUImgbAcCFsChMjY2DK2ZbxJli3J2qVWq/du9fa63/L7Vd35471+atliC+Z3Tp/url/9qup+695bdysBlF/oIwgCgKI/cbp635++/8+/ul/IDHWilXCFNxYRIWh6eatEeE2usJyVsX4xYDynAEhjuJUBjTgyppec6SErHcSmC4NlXo8yUz2MYFAUa3KsydxCJnSiskQtlKiGeUrhPOUwtWp80xj/uQPiOQHg0g6BiCFvNpIzvcSmiNcyNZ2jGmZJdJFUlwjqG1N7wNT/loAjxpo8Oemn1awlNj04LIvhPHP+BGU/vmrZPBdL/3kBuLTnVmLa7HbydgNKylx6jLIfR0mf9Q1iQD2d0R6W/TiVMA3iQJ/ZF2Ip0Oa2UzBbEGAuHOFicrQxkrmimF1a18rzo0n8VwOwsuuCpei2kzO9lMNFFvxpvJZX9bOr+nsUJTIFrOS4xv0OY/4hJvkhNT9L0ATBNL5RlHAZgXm7jl73Apy0MJU+wlx6qglEncwrgbECFHBFnfQzA7CipQM500PR7aIW5pj1RwmarCLaAAHFX/oMEBWMibkp/jiVMEHBbGGUezle+SxGLAF/2YoMrkGcNsfKmV76oxdjxXC+9j1qYbE5rxKITZEW04XXlEq4SKILq977nweAle6GzmgXjhyz6dMkWmq8rcvz6kkypot2GaTP/BIFt4mn/EfoYjf98ks8VvsTuu0NbLd3cDh8goLbwWD4t4z6HzCljzCaA2ku3NfjkGoFW68q2seShYRDawJU+wlx6qglEncwrgbECFHBFnfQzA7CipQM500PR7aIW5pj1RwmarCLaAAHFX/oMEBWMibkp/jiVMEHBbGGUezle+SxGLAF/2YoMrkGcNsfKmV76oxdjxXC+9j1qYbE5rxKITZEW04XXlEq4SKILq977nweAle6GzmgXjhyz6dMkWmq8rcvz6kkypot2GaTP/BIFt4mn/EfoYjf98ks8VvsTuu0NbLd3cDh8goLbwWD4t4z6HzCljzCaA2ku3NfjkGoFW68q2seShYRDawJU+wlx6qglEncwrgbECFHBFnfQzA7CipQM500PR7aIW5pj1RwmarCLaAAHFX/oMEBWMibkp/jiVMEHBbGGUezle+SxGLAF/2YoMrkGcNsfKmV76oxdjxXC+9j1qYbE5rxKITZEW04XXlEq4SKILq977nweAle6GzmgXjhyz6dMkWmq8rcvz6kkypot2GaTP/BIFt4mn/EfoYjf98ks8VvsTuu0NbLd3cDh8goLbwWD4t4z6HzCljzCaA2ku3NfjkGoFW68q2seShYRDawJU+wlx6qglEncwrgbECFHBFnfQzA7CipQM500PR7aIW5pj1RwmarCLaAAHFX/oMEBWMibkp/jiVMEHBbGGUezle+SxGLAF/2YoMrkGcNsfKmV76oxdjxXC+9j1qYbE5rxKITZEW04XXlEq4SKILq977nweAle6GzmgXjhyz6dMkWmq8rcvz6kkypot2GaTP/BIFt4mn/EfoYjf98ks8VvsTuu0NbLd3cDh8goLbwWD4t4z6HzCljzCaA2ku3NfjkGoFW68q2seShYRDawJe"

# ── Config persistente ────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(sys.executable), "torrent_stream_config.txt")

def load_download_path():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            path = f.read().strip()
            if os.path.exists(path):
                return path
    return os.path.join(os.environ.get("TEMP", "C:\\Temp"), "TorrentStream")

def save_download_path(path):
    with open(CONFIG_FILE, "w") as f:
        f.write(path)

# ── Janela de configuração ────────────────────────────────────────────────────
def show_config_window():
    root = tk.Tk()
    root.title("TorrentStream - Configuração")
    root.geometry("520x370")
    root.resizable(False, False)
    root.configure(bg="#1e1e2e")

    selected_path = tk.StringVar(value=load_download_path())
    temp_var = tk.BooleanVar(value=True)
    result = {"start": False}

    tk.Label(root, text="🎬 TorrentStream", font=("Segoe UI", 18, "bold"),
             bg="#1e1e2e", fg="#cdd6f4").pack(pady=(20, 4))
    tk.Label(root, text="Servidor de streaming via torrent",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#a6adc8").pack()

    chk_frame = tk.Frame(root, bg="#1e1e2e")
    chk_frame.pack(anchor="w", padx=30, pady=(16, 0))

    def toggle_entry():
        entry.config(state="normal")

    tk.Checkbutton(
        chk_frame, text="Usar pasta temporária (deletar arquivos ao fechar)",
        variable=temp_var, bg="#1e1e2e", fg="#a6e3a1", selectcolor="#313244",
        activebackground="#1e1e2e", activeforeground="#a6e3a1",
        font=("Segoe UI", 9), cursor="hand2", command=toggle_entry
    ).pack()

    tk.Label(root, text="Pasta para os arquivos (temporários ou permanentes):",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#cdd6f4").pack(anchor="w", padx=30, pady=(12, 4))

    frame = tk.Frame(root, bg="#1e1e2e")
    frame.pack(fill="x", padx=30)

    entry = tk.Entry(frame, textvariable=selected_path, font=("Segoe UI", 9),
                     bg="#313244", fg="#cdd6f4", insertbackground="white",
                     relief="flat", bd=6)
    entry.pack(side="left", fill="x", expand=True)

    def browse():
        path = filedialog.askdirectory(title="Escolha a pasta de download")
        if path:
            selected_path.set(path)

    tk.Button(frame, text="📂", command=browse, bg="#45475a", fg="#cdd6f4",
              relief="flat", font=("Segoe UI", 10), cursor="hand2",
              padx=8).pack(side="left", padx=(6, 0))

    tk.Label(root, text="⚠ Modo temporário: cria subpasta oculta '.torrentstream_temp' e deleta ao fechar.",
             font=("Segoe UI", 8), bg="#1e1e2e", fg="#6c7086", wraplength=460).pack(anchor="w", padx=30)

    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(pady=20)

    def start():
        base = selected_path.get().strip()
        if not base:
            messagebox.showwarning("Atenção", "Escolha uma pasta.")
            return
        if temp_var.get():
            path = os.path.join(base, ".torrentstream_temp")
        else:
            path = base
            save_download_path(path)
        os.makedirs(path, exist_ok=True)
        result["start"] = True
        result["path"] = path
        result["temporary"] = temp_var.get()
        root.destroy()

    def uninstall():
        if messagebox.askyesno(
            "Desinstalar",
            "Isso vai deletar:\n\n"
            "• A pasta de downloads\n"
            "• O arquivo de configuração\n"
            "• O próprio executável\n\n"
            "Tem certeza?"
        ):
            dl_path = selected_path.get().strip()
            if os.path.exists(dl_path):
                shutil.rmtree(dl_path, ignore_errors=True)
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            exe = sys.executable
            bat = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "uninstall_ts.bat")
            with open(bat, "w") as f:
                f.write('@echo off\ntimeout /t 2 /nobreak >nul\n')
                f.write(f'del /f /q "{exe}"\ndel /f /q "%~f0"\n')
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

# ── System Tray ───────────────────────────────────────────────────────────────
def run_tray(download_path, is_temporary, stop_event):
    try:
        import pystray
        from PIL import Image as PILImage

        img_bytes = base64.b64decode(ICON_B64)
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA").resize((64, 64), PILImage.LANCZOS)

        def on_open_folder(icon, item):
            os.startfile(download_path)

        def on_status(icon, item):
            torrents = ses.get_torrents()
            if not torrents:
                messagebox.showinfo("Status", "Nenhum torrent ativo.")
            else:
                lines = []
                for h in torrents:
                    s = h.status()
                    lines.append(f"{s.name}\n  {s.progress*100:.1f}% — {s.download_rate/1024:.0f} KB/s")
                messagebox.showinfo("Torrents ativos", "\n\n".join(lines))

        def on_quit(icon, item):
            icon.stop()
            cleanup_all()
            stop_event.set()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("🎬 TorrentStream — rodando", None, enabled=False),
            pystray.MenuItem(f"📁 {'Temp' if is_temporary else download_path}", on_open_folder),
            pystray.MenuItem("📊 Ver torrents ativos", on_status),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⏹ Parar servidor", on_quit),
        )

        icon = pystray.Icon("TorrentStream", img, "TorrentStream", menu)
        icon.run()

    except ImportError:
        print("⚠ pystray não encontrado, rodando sem system tray.")
        stop_event.wait()

# ── Análise de trilhas com ffprobe ────────────────────────────────────────────
def get_track_info(file_path):
    """
    Usa ffprobe para extrair informações de trilhas de áudio e legendas do arquivo.
    Retorna dict com audio_tracks e subtitle_tracks.
    """
    import subprocess, json

    result = {"audio_tracks": [], "subtitle_tracks": [], "ffprobe_available": False}

    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            file_path
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return result

        data = json.loads(proc.stdout)
        result["ffprobe_available"] = True
        streams = data.get("streams", [])

        for s in streams:
            codec_type = s.get("codec_type", "")
            tags = s.get("tags", {})
            lang = tags.get("language", tags.get("LANGUAGE", "und"))
            title = tags.get("title", tags.get("TITLE", ""))
            index = s.get("index", 0)
            codec = s.get("codec_name", "unknown")

            if codec_type == "audio":
                channels = s.get("channels", 0)
                channel_layout = s.get("channel_layout", "")
                sample_rate = s.get("sample_rate", "")
                bit_rate = s.get("bit_rate", "")

                # Determina label de canais
                if channels == 1:
                    ch_label = "Mono"
                elif channels == 2:
                    ch_label = "Stereo"
                elif channels == 6:
                    ch_label = "5.1 Surround"
                elif channels == 8:
                    ch_label = "7.1 Surround"
                else:
                    ch_label = f"{channels}ch" if channels > 0 else channel_layout

                result["audio_tracks"].append({
                    "index": index,
                    "language": lang,
                    "title": title,
                    "codec": codec.upper(),
                    "channels": channels,
                    "channel_layout": channel_layout,
                    "channel_label": ch_label,
                    "sample_rate": sample_rate,
                    "bit_rate": bit_rate,
                    "is_default": s.get("disposition", {}).get("default", 0) == 1,
                    "is_forced": s.get("disposition", {}).get("forced", 0) == 1,
                })

            elif codec_type == "subtitle":
                result["subtitle_tracks"].append({
                    "index": index,
                    "language": lang,
                    "title": title,
                    "codec": codec.upper(),
                    "is_default": s.get("disposition", {}).get("default", 0) == 1,
                    "is_forced": s.get("disposition", {}).get("forced", 0) == 1,
                    "is_hearing_impaired": s.get("disposition", {}).get("hearing_impaired", 0) == 1,
                })

    except FileNotFoundError:
        # ffprobe não instalado
        result["ffprobe_available"] = False
    except Exception as e:
        print(f"Erro no ffprobe: {e}")

    return result


# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY = True

# Dicionário global: info_hash -> {handle, file_path, file_size, track_info}
active_streams = {}
active_streams_lock = threading.Lock()

CONTENT_TYPES = {
    '.mp4': 'video/mp4',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
    '.webm': 'video/webm',
    '.mov': 'video/quicktime',
    '.m4v': 'video/mp4',
    '.ts':  'video/mp2t',
}

def cleanup_torrent(handle, file_path):
    try:
        ses.remove_torrent(handle)
    except Exception:
        pass
    if IS_TEMPORARY and file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            print(f"🗑 Deletado: {file_path}")
            folder = os.path.dirname(file_path)
            if os.path.isdir(folder) and not os.listdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
        except Exception as e:
            print(f"Erro ao deletar: {e}")

def cleanup_all():
    if IS_TEMPORARY and os.path.exists(DOWNLOAD_PATH):
        shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
        print(f"🗑 Pasta temporária deletada: {DOWNLOAD_PATH}")


def stream_file(file_path, file_size, content_type):
    """
    Gera resposta HTTP com suporte a Range requests (essencial para MKV/seeking).
    """
    range_header = request.headers.get("Range", None)

    if range_header:
        # Parse: "bytes=START-END"
        byte_range = range_header.strip().replace("bytes=", "")
        parts = byte_range.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        def generate_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk_size = min(1024 * 256, remaining)  # 256 KB chunks
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
        }
        return Response(generate_range(), status=206, headers=headers)

    else:
        # Stream completo
        def generate_full():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 256)
                    if not chunk:
                        break
                    yield chunk

        headers = {
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
        }
        return Response(generate_full(), status=200, headers=headers)


@app.route("/play")
def play():
    magnet = request.args.get("magnet")
    if not magnet:
        return jsonify({"error": "No magnet link provided"}), 400

    params = {
        'save_path': DOWNLOAD_PATH,
        'storage_mode': lt.storage_mode_t(2)
    }
    handle = lt.add_magnet_uri(ses, magnet, params)
    print("Baixando metadata...")

    elapsed = 0
    while not handle.has_metadata():
        time.sleep(1)
        elapsed += 1
        if elapsed >= 60:
            return jsonify({"error": "Timeout ao buscar metadata"}), 504

    torrent_info = handle.get_torrent_info()
    files = torrent_info.files()

    # Seleciona o maior arquivo de vídeo
    video_exts = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.m4v', '.ts'}
    best_index = -1
    best_size = -1
    for i in range(files.num_files()):
        ext = os.path.splitext(files.file_path(i))[1].lower()
        size = files.file_size(i)
        if ext in video_exts and size > best_size:
            best_size = size
            best_index = i

    if best_index == -1:
        # Fallback: maior arquivo geral
        best_index = max(range(files.num_files()), key=lambda i: files.file_size(i))

    # Prioriza apenas o arquivo escolhido
    for i in range(files.num_files()):
        handle.file_priority(i, 0)
    handle.file_priority(best_index, 7)

    file_path = os.path.join(DOWNLOAD_PATH, files.file_path(best_index))
    file_size = files.file_size(best_index)
    ext = os.path.splitext(file_path)[1].lower()
    content_type = CONTENT_TYPES.get(ext, 'video/mp4')

    print(f"Arquivo: {file_path} ({file_size/1024/1024:.1f} MB) [{content_type}]")

    # Armazena referência para /info e /stop
    info_hash = str(handle.status().info_hash).lower()
    with active_streams_lock:
        active_streams[info_hash] = {
            "handle": handle,
            "file_path": file_path,
            "file_size": file_size,
            "content_type": content_type,
            "track_info": None,  # será populado após download inicial
        }

    # Aguarda buffer inicial (5%)
    waited = 0
    while True:
        progress = handle.status().progress
        if progress > 0.05:
            break
        time.sleep(1)
        waited += 1
        if waited > 120:
            break

    print("Iniciando stream...")
    return stream_file(file_path, file_size, content_type)


@app.route("/stream/<info_hash>")
def stream_by_hash(info_hash):
    """
    Endpoint de stream direto por info_hash (para re-requests / seeking).
    Suporta Range requests completo.
    """
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)

    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    file_size = entry["file_size"]
    content_type = entry["content_type"]

    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503

    return stream_file(file_path, file_size, content_type)


@app.route("/info/<info_hash>")
def track_info(info_hash):
    """
    Retorna informações de trilhas de áudio e legendas via ffprobe.
    Aguarda até o arquivo ter pelo menos 10% para análise.
    """
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)

    if not entry:
        # Tenta encontrar pelo torrent ativo
        torrents = ses.get_torrents()
        for h in torrents:
            s = h.status()
            if str(s.info_hash).lower() == info_hash:
                return jsonify({"error": "Torrent encontrado mas stream não iniciado via /play"}), 400
        return jsonify({"error": "Torrent não encontrado"}), 404

    handle = entry["handle"]
    file_path = entry["file_path"]
    file_size = entry["file_size"]

    # Retorna cache se já analisado
    if entry.get("track_info"):
        return jsonify(entry["track_info"])

    # Aguarda arquivo existir
    waited = 0
    while not os.path.exists(file_path):
        time.sleep(1)
        waited += 1
        if waited > 30:
            return jsonify({"error": "Arquivo não encontrado no disco"}), 503

    # Para MKV: aguarda pelo menos 5% para ffprobe conseguir ler o header
    waited = 0
    while handle.status().progress < 0.05:
        time.sleep(2)
        waited += 2
        if waited > 60:
            break

    tracks = get_track_info(file_path)

    # Adiciona info do torrent
    s = handle.status()
    result = {
        "info_hash": info_hash,
        "name": s.name,
        "file_path": file_path,
        "file_size": file_size,
        "file_size_mb": round(file_size / 1024 / 1024, 1),
        "content_type": entry["content_type"],
        "extension": os.path.splitext(file_path)[1].lower(),
        "stream_url": f"/stream/{info_hash}",
        "progress": round(s.progress * 100, 1),
        **tracks,
    }

    # Cacheia
    with active_streams_lock:
        active_streams[info_hash]["track_info"] = result

    return jsonify(result)


@app.route("/status")
def status():
    torrents = ses.get_torrents()
    result = []
    for h in torrents:
        s = h.status()
        ih = str(s.info_hash).lower()
        with active_streams_lock:
            entry = active_streams.get(ih, {})

        result.append({
            'name': s.name,
            'info_hash': ih,
            'progress': round(s.progress * 100, 1),
            'progress_str': f"{s.progress*100:.1f}%",
            'download_rate_kbps': round(s.download_rate / 1024, 1),
            'download_rate_str': f"{s.download_rate/1024:.1f} KB/s",
            'peers': s.num_peers,
            'upload_rate_kbps': round(s.upload_rate / 1024, 1),
            'upload_rate_str': f"{s.upload_rate/1024:.1f} KB/s",
            'state': str(s.state),
            'stream_url': f"/stream/{ih}" if ih in active_streams else None,
            'info_url': f"/info/{ih}" if ih in active_streams else None,
            'has_track_info': bool(entry.get("track_info")),
        })
    return jsonify({
        "status": "online",
        "torrents": result,
        "download_path": DOWNLOAD_PATH,
        "temporary": IS_TEMPORARY
    })


@app.route("/ping")
def ping():
    return jsonify({"status": "online", "version": "2.0.0"})


@app.route("/stop", methods=["POST"])
def stop_torrent():
    data = request.get_json(silent=True) or {}
    info_hash = data.get("info_hash", "").strip().lower()

    if not info_hash:
        return jsonify({"error": "info_hash é obrigatório"}), 400

    with active_streams_lock:
        entry = active_streams.pop(info_hash, None)

    if entry:
        threading.Thread(
            target=cleanup_torrent,
            args=(entry["handle"], entry["file_path"]),
            daemon=True
        ).start()
        return jsonify({"success": True, "stopped": entry["handle"].status().name})

    # Fallback: procura nos torrents ativos
    torrents = ses.get_torrents()
    for h in torrents:
        s = h.status()
        if str(s.info_hash).lower() == info_hash:
            threading.Thread(target=cleanup_torrent, args=(h, ""), daemon=True).start()
            return jsonify({"success": True, "stopped": s.name})

    return jsonify({"error": "Torrent não encontrado"}), 404


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = show_config_window()
    if not result.get("start"):
        sys.exit(0)

    DOWNLOAD_PATH = result["path"]
    IS_TEMPORARY = result["temporary"]
    mode = "temporário" if IS_TEMPORARY else "permanente"
    print(f"📁 Salvando em: {DOWNLOAD_PATH} [{mode}]")
    print(f"🚀 Servidor em http://0.0.0.0:5000")

    stop_event = threading.Event()

    tray_thread = threading.Thread(
        target=run_tray,
        args=(DOWNLOAD_PATH, IS_TEMPORARY, stop_event),
        daemon=True
    )
    tray_thread.start()

    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, threaded=True),
        daemon=True
    )
    flask_thread.start()

    stop_event.wait()
    cleanup_all()
    sys.exit(0)