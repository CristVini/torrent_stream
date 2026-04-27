import sys
import os
import threading
import time
import shutil
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

# Importações dos módulos locais
import config
from core.torrent import ses, active_streams, active_streams_lock, bootstrap_magnet, wait_for_buffer
from core.ffmpeg import start_hls_transcode, kill_ffmpeg_procs
from engines.stremio import resolve_imdb_id, resolve_kitsu_id
from engines.nyaa import search_nyaa
from sse import sse_global, sse_hash, _sse_broadcast_hash
from core.cast import cast_manager

app = Flask(__name__)
CORS(app)

# ── LÓGICA DE LIMPEZA AUTOMÁTICA ───────────────────────────────────────────
cleanup_interval_hours = 2
last_cleanup_time = time.time()

def auto_cleanup_worker():
    global last_cleanup_time
    while True:
        try:
            current_time = time.time()
            if current_time - last_cleanup_time >= (cleanup_interval_hours * 3600):
                # Limpa cache HLS
                if config.HLS_CACHE_PATH and os.path.exists(config.HLS_CACHE_PATH):
                    for item in os.listdir(config.HLS_CACHE_PATH):
                        item_path = os.path.join(config.HLS_CACHE_PATH, item)
                        try:
                            if os.path.isdir(item_path): shutil.rmtree(item_path)
                            else: os.remove(item_path)
                        except Exception: pass
                
                # Limpa downloads antigos
                if config.DOWNLOAD_PATH and os.path.exists(config.DOWNLOAD_PATH):
                    for item in os.listdir(config.DOWNLOAD_PATH):
                        if item == "hls_cache": continue
                        item_path = os.path.join(config.DOWNLOAD_PATH, item)
                        try:
                            mtime = os.path.getmtime(item_path)
                            if current_time - mtime >= (cleanup_interval_hours * 3600):
                                if os.path.isdir(item_path): shutil.rmtree(item_path)
                                else: os.remove(item_path)
                        except Exception: pass
                last_cleanup_time = current_time
        except Exception: pass
        time.sleep(300)

# ── ROTAS FLASK ─────────────────────────────────────────────────────────────
@app.route("/status")
def status_api():
    return jsonify({
        "status": "online", 
        "torrents": len(active_streams),
        "cleanup_interval": f"{cleanup_interval_hours}h",
        "addons": config.STREMIO_ADDONS
    })

@app.route("/addons/start", methods=["POST"])
def addon_start():
    data = request.get_json(silent=True) or {}
    ih = data.get("infoHash", "").strip().lower()
    magnet = data.get("magnet", "").strip() or f"magnet:?xt=urn:btih:{ih}"
    try:
        handle, info_hash, file_path, file_size, content_type = bootstrap_magnet(magnet)
        if not wait_for_buffer(file_path):
            return jsonify({"error": "Buffer timeout"}), 503
        resp = {"info_hash": info_hash, "name": handle.status().name}
        _sse_broadcast_hash(info_hash, "started", resp)
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/events/global")
def events_global(): return sse_global()

@app.route("/events/<info_hash>")
def events_hash(info_hash): return sse_hash(info_hash)

@app.route("/cast/devices")
def list_cast_devices():
    force = request.args.get("force", "false").lower() == "true"
    return jsonify({"devices": cast_manager.discover_devices(force=force)})

@app.route("/cast/play", methods=["POST"])
def cast_play():
    data = request.get_json(silent=True) or {}
    device_ip, url = data.get("ip"), data.get("url")
    if url and url.startswith("/"):
        url = f"http://{request.host.split(':')[0]}:5000{url}"
    return jsonify({"success": cast_manager.play_on_device(device_ip, url)})

# ── INTERFACE VISUAL ORIGINAL (RESTAURADA) ───────────────────────────────────

def show_config_window() -> dict:
    root = tk.Tk()
    root.title("TorrentStream – Configuração")
    root.geometry("520x450")
    root.resizable(False, False)
    root.configure(bg="#1e1e2e")

    selected_path = tk.StringVar(value=config.DOWNLOAD_PATH or os.path.join(os.path.expanduser("~"), "Downloads", "TorrentStream"))
    temp_var      = tk.BooleanVar(value=True)
    result        = {"start": False}

    tk.Label(root, text="🎬 TorrentStream", font=("Segoe UI", 18, "bold"),
             bg="#1e1e2e", fg="#cdd6f4").pack(pady=(20, 4))
    tk.Label(root, text="Servidor de streaming via torrent",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#a6adc8").pack()

    # Opção de Pasta Temporária
    chk_frame = tk.Frame(root, bg="#1e1e2e")
    chk_frame.pack(anchor="w", padx=30, pady=(16, 0))
    tk.Checkbutton(
        chk_frame,
        text="Usar pasta temporária (deletar arquivos ao fechar)",
        variable=temp_var, bg="#1e1e2e", fg="#a6e3a1", selectcolor="#313244",
        activebackground="#1e1e2e", activeforeground="#a6e3a1",
        font=("Segoe UI", 9), cursor="hand2",
    ).pack()

    # Seleção de Pasta
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
        if p: selected_path.set(p)

    tk.Button(frame, text="📂", command=browse, bg="#45475a", fg="#cdd6f4",
              relief="flat", font=("Segoe UI", 10), cursor="hand2",
              padx=8).pack(side="left", padx=(6, 0))

    # NOVO: Controle de Limpeza Automática
    tk.Label(root, text="Limpeza automática (mínimo 2h):",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#cdd6f4").pack(anchor="w", padx=30, pady=(12, 4))
    
    clean_frame = tk.Frame(root, bg="#1e1e2e")
    clean_frame.pack(fill="x", padx=30)
    hours_var = tk.StringVar(value="2")
    tk.Spinbox(clean_frame, from_=2, to=72, textvariable=hours_var, font=("Segoe UI", 9),
               bg="#313244", fg="#cdd6f4", buttonbackground="#45475a", relief="flat", width=10).pack(side="left")
    tk.Label(clean_frame, text="horas", bg="#1e1e2e", fg="#a6adc8", font=("Segoe UI", 9)).pack(side="left", padx=5)

    tk.Label(root,
             text="⚠ Modo temporário: cria subpasta '.torrentstream_temp' e deleta ao fechar.",
             font=("Segoe UI", 8), bg="#1e1e2e", fg="#6c7086", wraplength=460,
             ).pack(anchor="w", padx=30, pady=(10, 0))

    # Botões de Ação
    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(pady=25)

    def start():
        global cleanup_interval_hours
        base = selected_path.get().strip()
        if not base:
            messagebox.showwarning("Atenção", "Escolha uma pasta.")
            return
        
        try:
            val = int(hours_var.get())
            cleanup_interval_hours = max(2, val)
        except: pass

        path = os.path.join(base, ".torrentstream_temp") if temp_var.get() else base
        config.DOWNLOAD_PATH = path
        config.IS_TEMPORARY = temp_var.get()
        config.HLS_CACHE_PATH = os.path.join(path, "hls_cache")
        
        os.makedirs(path, exist_ok=True)
        os.makedirs(config.HLS_CACHE_PATH, exist_ok=True)
        
        config.save_settings(download_path=base)
        result.update({"start": True})
        root.destroy()

    tk.Button(btn_frame, text="▶  Iniciar Servidor", command=start,
              bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
              relief="flat", cursor="hand2", padx=20, pady=8).pack(side="left", padx=8)

    root.mainloop()
    return result

def run_tray(stop_event: threading.Event) -> None:
    try:
        import pystray
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (64, 64), color=(30, 30, 80))

        def on_open_folder(icon, item):
            if os.path.exists(config.DOWNLOAD_PATH):
                os.startfile(config.DOWNLOAD_PATH)

        def on_status(icon, item):
            with active_streams_lock:
                if not active_streams:
                    messagebox.showinfo("Status", "Nenhum torrent ativo.")
                else:
                    lines = []
                    for ih, entry in active_streams.items():
                        handle = entry.get("handle")
                        if handle:
                            s = handle.status()
                            lines.append(f"{s.name}\n  {s.progress*100:.1f}% — {s.download_rate/1024:.0f} KB/s")
                    messagebox.showinfo("Torrents ativos", "\n\n".join(lines) if lines else "Nenhum torrent ativo.")

        def on_quit(icon, item):
            icon.stop()
            if config.IS_TEMPORARY and os.path.exists(config.DOWNLOAD_PATH):
                shutil.rmtree(config.DOWNLOAD_PATH, ignore_errors=True)
            stop_event.set()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("🎬 TorrentStream", None, enabled=False),
            pystray.MenuItem(f"📁 Abrir Pasta", on_open_folder),
            pystray.MenuItem("📊 Status", on_status),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⏹ Parar", on_quit),
        )
        pystray.Icon("TorrentStream", img, "TorrentStream", menu).run()

    except ImportError:
        stop_event.wait()

def run_flask():
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)

if __name__ == "__main__":
    # 1. Mostra a janela de configuração original
    res = show_config_window()
    if not res.get("start"):
        sys.exit(0)

    # 2. Inicia os serviços em background
    stop_event = threading.Event()
    
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=auto_cleanup_worker, daemon=True).start()
    threading.Thread(target=run_tray, args=(stop_event,), daemon=True).start()
    
    print(f"🚀 Servidor rodando em http://0.0.0.0:5000")
    print(f"📁 Pasta: {config.DOWNLOAD_PATH}")
    
    # 3. Mantém o processo vivo até o tray fechar
    stop_event.wait()
