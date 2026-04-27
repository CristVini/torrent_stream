import threading
import sys
import os
import json
import time
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

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
                if config.HLS_CACHE_PATH and os.path.exists(config.HLS_CACHE_PATH):
                    for item in os.listdir(config.HLS_CACHE_PATH):
                        item_path = os.path.join(config.HLS_CACHE_PATH, item)
                        try:
                            if os.path.isdir(item_path): shutil.rmtree(item_path)
                            else: os.remove(item_path)
                        except Exception: pass
                
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
def status():
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

# ── INTERFACE GRÁFICA (TKINTER) ──────────────────────────────────────────────

class TorrentStreamGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("TorrentStream Server Control")
        self.root.geometry("700x650")
        
        # Notebook para abas
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Aba Principal
        self.tab_main = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_main, text="Geral")
        self.setup_main_tab()
        
        # Aba de Addons
        self.tab_addons = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_addons, text="Stremio Addons")
        self.setup_addons_tab()

        self.update_gui()

    def setup_main_tab(self):
        frame = ttk.Frame(self.tab_main, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="TorrentStream Engine", font=("Helvetica", 16, "bold")).pack(pady=10)
        self.status_label = ttk.Label(frame, text="Servidor: Online (Porta 5000)", foreground="green")
        self.status_label.pack(pady=5)
        
        # Pasta
        folder_frame = ttk.LabelFrame(frame, text="Pasta de Arquivos Temporários", padding=10)
        folder_frame.pack(fill=tk.X, pady=10)
        self.path_var = tk.StringVar(value=config.DOWNLOAD_PATH)
        ttk.Label(folder_frame, textvariable=self.path_var, wraplength=500).pack(side=tk.LEFT, padx=5)
        ttk.Button(folder_frame, text="Selecionar", command=self.change_folder).pack(side=tk.RIGHT)
        
        # Limpeza
        cleanup_frame = ttk.LabelFrame(frame, text="Limpeza Automática", padding=10)
        cleanup_frame.pack(fill=tk.X, pady=10)
        ttk.Label(cleanup_frame, text="Intervalo (horas):").pack(side=tk.LEFT, padx=5)
        self.hours_var = tk.StringVar(value="2")
        ttk.Spinbox(cleanup_frame, from_=2, to=72, width=5, textvariable=self.hours_var, command=self.update_cleanup_interval).pack(side=tk.LEFT, padx=5)
        self.next_cleanup_label = ttk.Label(cleanup_frame, text="Próxima em: -- min", foreground="blue")
        self.next_cleanup_label.pack(side=tk.RIGHT, padx=5)
        
        # Torrents
        list_frame = ttk.LabelFrame(frame, text="Torrents Ativos", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.tree = ttk.Treeview(list_frame, columns=("Hash", "Status"), show="headings", height=5)
        self.tree.heading("Hash", text="Info Hash")
        self.tree.heading("Status", text="Status")
        self.tree.pack(fill=tk.BOTH, expand=True)

    def setup_addons_tab(self):
        frame = ttk.Frame(self.tab_addons, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Gerenciar Addons do Stremio", font=("Helvetica", 12, "bold")).pack(pady=10)
        
        # Lista de Addons
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        self.addon_listbox = tk.Listbox(list_frame, height=10)
        self.addon_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.addon_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.addon_listbox.config(yscrollcommand=scrollbar.set)
        
        # Carregar addons iniciais
        for addon in config.STREMIO_ADDONS:
            self.addon_listbox.insert(tk.END, addon)
            
        # Controles
        ctrl_frame = ttk.Frame(frame, padding=10)
        ctrl_frame.pack(fill=tk.X)
        
        self.new_addon_var = tk.StringVar()
        ttk.Entry(ctrl_frame, textvariable=self.new_addon_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(ctrl_frame, text="Adicionar URL", command=self.add_addon).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Remover Selecionado", command=self.remove_addon).pack(side=tk.LEFT, padx=5)

    def add_addon(self):
        url = self.new_addon_var.get().strip()
        if url and url.startswith("http"):
            if url not in config.STREMIO_ADDONS:
                config.STREMIO_ADDONS.append(url)
                self.addon_listbox.insert(tk.END, url)
                config.save_settings(addons=config.STREMIO_ADDONS)
                self.new_addon_var.set("")
            else:
                messagebox.showwarning("Aviso", "Esta URL já existe.")
        else:
            messagebox.showerror("Erro", "Insira uma URL válida (começando com http).")

    def remove_addon(self):
        selection = self.addon_listbox.curselection()
        if selection:
            idx = selection[0]
            url = self.addon_listbox.get(idx)
            config.STREMIO_ADDONS.remove(url)
            self.addon_listbox.delete(idx)
            config.save_settings(addons=config.STREMIO_ADDONS)
        else:
            messagebox.showwarning("Aviso", "Selecione um addon para remover.")

    def change_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            config.DOWNLOAD_PATH = folder
            self.path_var.set(folder)
            config.HLS_CACHE_PATH = os.path.join(folder, "hls_cache")
            os.makedirs(config.HLS_CACHE_PATH, exist_ok=True)
            config.save_settings(download_path=folder)

    def update_cleanup_interval(self):
        global cleanup_interval_hours
        try:
            val = int(self.hours_var.get())
            if val < 2: val = 2
            cleanup_interval_hours = val
        except ValueError: pass

    def update_gui(self):
        remaining_sec = (cleanup_interval_hours * 3600) - (time.time() - last_cleanup_time)
        remaining_min = max(0, int(remaining_sec // 60))
        self.next_cleanup_label.config(text=f"Próxima em: {remaining_min} min")
        
        for item in self.tree.get_children(): self.tree.delete(item)
        with active_streams_lock:
            for ih in active_streams.keys():
                self.tree.insert("", tk.END, values=(ih[:20], "Ativo"))
        
        self.root.after(10000, self.update_gui)

def run_flask():
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)

if __name__ == "__main__":
    if not config.DOWNLOAD_PATH:
        config.DOWNLOAD_PATH = os.path.join(os.path.expanduser("~"), "Downloads", "TorrentStream")
    os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
    config.HLS_CACHE_PATH = os.path.join(config.DOWNLOAD_PATH, "hls_cache")
    os.makedirs(config.HLS_CACHE_PATH, exist_ok=True)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=auto_cleanup_worker, daemon=True).start()
    
    root = tk.Tk()
    gui = TorrentStreamGUI(root)
    root.mainloop()
