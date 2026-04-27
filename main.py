import threading
import sys
import os
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

# ── ROTAS ───────────────────────────────────────────────────────────────────

@app.route("/status")
def status():
    # Reutiliza a lógica de snapshot
    return jsonify({"status": "online"})

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

# Adicionar as rotas SSE aqui chamando as funções do sse.py
@app.route("/events/global")
def events_global():
    return sse_global()

@app.route("/events/<info_hash>")
def events_hash(info_hash):
    return sse_hash(info_hash)

# ── DLNA / CASTING ──────────────────────────────────────────────────────────

@app.route("/cast/devices")
def list_cast_devices():
    """Lista TVs e dispositivos DLNA na rede local."""
    force = request.args.get("force", "false").lower() == "true"
    devices = cast_manager.discover_devices(force=force)
    return jsonify({"devices": devices})

@app.route("/cast/play", methods=["POST"])
def cast_play():
    """Envia o link HLS ou Stream para a TV."""
    data = request.get_json(silent=True) or {}
    device_ip = data.get("ip")
    url = data.get("url")
    
    if not device_ip or not url:
        return jsonify({"error": "IP do dispositivo e URL são obrigatórios"}), 400
    
    # Se for um link relativo, transforma em absoluto usando o IP do servidor
    if url.startswith("/"):
        server_ip = request.host.split(":")[0]
        url = f"http://{server_ip}:5000{url}"

    success = cast_manager.play_on_device(device_ip, url)
    return jsonify({"success": success})

@app.route("/cast/stop", methods=["POST"])
def cast_stop():
    data = request.get_json(silent=True) or {}
    device_ip = data.get("ip")
    if not device_ip:
        return jsonify({"error": "IP do dispositivo é obrigatório"}), 400
    success = cast_manager.stop_device(device_ip)
    return jsonify({"success": success})

if __name__ == "__main__":
    # Aqui chamaria o show_config_window() e inicializaria o config.DOWNLOAD_PATH
    # Por agora, apenas um exemplo de inicialização
    config.DOWNLOAD_PATH = "/tmp/TorrentStream"
    os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
    
    app.run(host="0.0.0.0", port=5000, threaded=True)
