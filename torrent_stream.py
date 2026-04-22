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

# 🔥 NOVO
import requests
from concurrent.futures import ThreadPoolExecutor

# ── ADDONS CONFIG ──────────────────────────────────────────────────────────
ADDONS = [
    "https://torrentio.strem.fun",
    "https://mediafusion.elfhosted.com",
    "https://comet.elfhosted.com"
]

# ── SESSION ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY = True

# ── ADDONS ENGINE ──────────────────────────────────────────────────────────
def fetch_addon_streams(addon_url, type_, id_):
    try:
        url = f"{addon_url}/stream/{type_}/{id_}.json"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)

        if res.status_code != 200:
            return []

        data = res.json()
        streams = data.get("streams", [])

        for s in streams:
            s["source"] = addon_url

        return streams

    except Exception as e:
        print(f"Erro no addon {addon_url}: {e}")
        return []


def get_all_streams(type_, id_):
    all_streams = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(fetch_addon_streams, addon, type_, id_)
            for addon in ADDONS
        ]

        for future in futures:
            result = future.result()
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
    info_hash = stream.get("infoHash")

    if not info_hash:
        return None

    trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://tracker.openbittorrent.com:80/announce"
    ]

    tr = "&".join([f"tr={t}" for t in trackers])
    return f"magnet:?xt=urn:btih:{info_hash}&{tr}"

# ── PLAYER CENTRAL (UNIFICADO) ─────────────────────────────────────────────
def play_with_magnet(magnet):
    params = {
        'save_path': DOWNLOAD_PATH,
        'storage_mode': lt.storage_mode_t(2)
    }

    handle = lt.add_magnet_uri(ses, magnet, params)

    # metadata
    for _ in range(60):
        if handle.has_metadata():
            break
        time.sleep(1)
    else:
        return jsonify({"error": "metadata timeout"}), 504

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

        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)

                    if not chunk:
                        time.sleep(0.5)
                        continue

                    yield chunk

        finally:
            threading.Thread(
                target=cleanup_torrent,
                args=(handle, file_path),
                daemon=True
            ).start()

    return Response(
        stream(),
        content_type="video/mp4",
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*"
        }
    )

# ── CLEANUP ────────────────────────────────────────────────────────────────
def cleanup_torrent(handle, file_path):
    try:
        ses.remove_torrent(handle)
    except:
        pass

    if IS_TEMPORARY and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except:
            pass


def cleanup_all():
    if IS_TEMPORARY and os.path.exists(DOWNLOAD_PATH):
        shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)

# ── ROTAS ──────────────────────────────────────────────────────────────────

# ✔ PLAY NORMAL
@app.route("/play")
def play():
    magnet = request.args.get("magnet")
    if not magnet:
        return jsonify({"error": "magnet missing"}), 400

    return play_with_magnet(magnet)


# ✔ BUSCAR STREAMS DOS ADDONS
@app.route("/addons/streams")
def addon_streams():
    type_ = request.args.get("type", "series")
    id_ = request.args.get("id")

    if not id_:
        return jsonify({"error": "id obrigatório"}), 400

    streams = get_all_streams(type_, id_)

    result = []
    for s in streams:
        result.append({
            "title": s.get("title"),
            "quality": s.get("quality"),
            "infoHash": s.get("infoHash"),
            "magnet": build_magnet(s),
            "source": s.get("source")
        })

    return jsonify({
        "total": len(result),
        "streams": result
    })


# ✔ PLAY VIA ADDON
@app.route("/addons/play")
def addon_play():
    info_hash = request.args.get("infoHash")

    if not info_hash:
        return jsonify({"error": "infoHash obrigatório"}), 400

    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    return play_with_magnet(magnet)

# ── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    DOWNLOAD_PATH = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "TorrentStream")
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

    print(f"📁 Salvando em: {DOWNLOAD_PATH}")
    print(f"🚀 Servidor em http://0.0.0.0:5000")

    app.run(host="0.0.0.0", port=5000, threaded=True)