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

import requests
from concurrent.futures import ThreadPoolExecutor

# ── CONFIG ─────────────────────────────────────────────────────────────────
ADDONS = [
    "https://torrentio.strem.fun",
    "https://mediafusion.elfhosted.com",
    "https://comet.elfhosted.com"
]

CONFIG_FILE = os.path.join(os.path.dirname(sys.executable), "torrent_stream_config.txt")

# ── FLASK + TORRENT SESSION ───────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY = True

# ── CONFIG FILE ────────────────────────────────────────────────────────────
def load_download_path():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            path = f.read().strip()
            if os.path.exists(path):
                return path
    return os.path.join(os.environ.get("TEMP", "/tmp"), "TorrentStream")

def save_download_path(path):
    with open(CONFIG_FILE, "w") as f:
        f.write(path)

# ── UI ─────────────────────────────────────────────────────────────────────
def show_config_window():
    root = tk.Tk()
    root.title("TorrentStream")
    root.geometry("520x370")
    root.configure(bg="#1e1e2e")

    selected_path = tk.StringVar(value=load_download_path())
    temp_var = tk.BooleanVar(value=True)
    result = {"start": False}

    tk.Label(root, text="🎬 TorrentStream", font=("Segoe UI", 18, "bold"),
             bg="#1e1e2e", fg="#cdd6f4").pack(pady=20)

    tk.Checkbutton(
        root,
        text="Modo temporário (apaga ao fechar)",
        variable=temp_var,
        bg="#1e1e2e",
        fg="#a6e3a1",
        selectcolor="#313244"
    ).pack()

    frame = tk.Frame(root, bg="#1e1e2e")
    frame.pack(padx=20, pady=20, fill="x")

    entry = tk.Entry(frame, textvariable=selected_path,
                     bg="#313244", fg="white")
    entry.pack(side="left", fill="x", expand=True)

    def browse():
        path = filedialog.askdirectory()
        if path:
            selected_path.set(path)

    tk.Button(frame, text="📂", command=browse).pack(side="left")

    def start():
        path = selected_path.get()
        if not path:
            messagebox.showerror("Erro", "Escolha uma pasta")
            return

        if temp_var.get():
            path = os.path.join(path, ".tmp")

        os.makedirs(path, exist_ok=True)

        result["start"] = True
        result["path"] = path
        result["temporary"] = temp_var.get()
        root.destroy()

    tk.Button(root, text="▶ Iniciar", command=start,
              bg="#89b4fa").pack(pady=20)

    root.mainloop()
    return result

# ── SYSTEM TRAY ────────────────────────────────────────────────────────────
def run_tray(download_path, stop_event):
    try:
        import pystray
        from PIL import Image

        image = Image.new("RGB", (64, 64), color=(40, 40, 40))

        def quit_app(icon, item):
            icon.stop()
            stop_event.set()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("TorrentStream rodando", None, enabled=False),
            pystray.MenuItem("Sair", quit_app)
        )

        icon = pystray.Icon("TS", image, "TorrentStream", menu)
        icon.run()

    except:
        stop_event.wait()

# ── ADDON ENGINE ───────────────────────────────────────────────────────────
def fetch_addon_streams(addon_url, type_, id_):
    try:
        url = f"{addon_url}/stream/{type_}/{id_}.json"
        res = requests.get(url, timeout=10)

        if res.status_code != 200:
            return []

        data = res.json()
        streams = data.get("streams", [])

        for s in streams:
            s["source"] = addon_url

        return streams
    except:
        return []

def get_all_streams(type_, id_):
    all_streams = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(fetch_addon_streams, addon, type_, id_)
            for addon in ADDONS
        ]

        for f in futures:
            result = f.result()
            if result:
                all_streams.extend(result)

    seen = set()
    unique = []

    for s in all_streams:
        ih = s.get("infoHash")
        if ih and ih not in seen:
            seen.add(ih)
            unique.append(s)

    return unique

def build_magnet(stream):
    ih = stream.get("infoHash")
    if not ih:
        return None
    return f"magnet:?xt=urn:btih:{ih}"

# ── TORRENT PLAYER ─────────────────────────────────────────────────────────
def play_with_magnet(magnet):
    params = {'save_path': DOWNLOAD_PATH}
    handle = lt.add_magnet_uri(ses, magnet, params)

    for _ in range(60):
        if handle.has_metadata():
            break
        time.sleep(1)
    else:
        return jsonify({"error": "timeout"}), 504

    info = handle.get_torrent_info()
    files = info.files()

    file_index = max(range(files.num_files()), key=lambda i: files.file_size(i))

    for i in range(files.num_files()):
        handle.file_priority(i, 0)
    handle.file_priority(file_index, 7)

    file_path = os.path.join(DOWNLOAD_PATH, files.file_path(file_index))
    file_size = files.file_size(file_index)

    def stream():
        while handle.status().progress < 0.05:
            time.sleep(1)

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    time.sleep(0.5)
                    continue
                yield chunk

    return Response(stream(), content_type="video/mp4")

# ── ROTAS ──────────────────────────────────────────────────────────────────
@app.route("/play")
def play():
    magnet = request.args.get("magnet")
    return play_with_magnet(magnet)

@app.route("/addons/streams")
def addon_streams():
    type_ = request.args.get("type", "series")
    id_ = request.args.get("id")

    streams = get_all_streams(type_, id_)

    return jsonify({
        "streams": [
            {
                "title": s.get("title"),
                "infoHash": s.get("infoHash"),
                "magnet": build_magnet(s)
            }
            for s in streams
        ]
    })

@app.route("/addons/play")
def addon_play():
    ih = request.args.get("infoHash")
    magnet = f"magnet:?xt=urn:btih:{ih}"
    return play_with_magnet(magnet)

# ── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = show_config_window()

    if not result.get("start"):
        sys.exit(0)

    DOWNLOAD_PATH = result["path"]
    IS_TEMPORARY = result["temporary"]

    stop_event = threading.Event()

    threading.Thread(
        target=run_tray,
        args=(DOWNLOAD_PATH, stop_event),
        daemon=True
    ).start()

    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000),
        daemon=True
    ).start()

    stop_event.wait()