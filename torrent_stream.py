import libtorrent as lt
import time
import os
from flask import Flask, request, Response, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Permite requisições de qualquer origem (necessário para TV/navegador)

ses = lt.session()
ses.listen_on(6881, 6891)

os.makedirs('./downloads', exist_ok=True)


@app.route("/play")
def play():
    magnet = request.args.get("magnet")
    if not magnet:
        return jsonify({"error": "No magnet link provided"}), 400

    params = {
        'save_path': './downloads',
        'storage_mode': lt.storage_mode_t(2),  # sparse storage
    }

    handle = lt.add_magnet_uri(ses, magnet, params)
    print("Baixando metadata...")

    # Aguarda metadata com timeout de 60s
    timeout = 60
    elapsed = 0
    while not handle.has_metadata():
        time.sleep(1)
        elapsed += 1
        if elapsed >= timeout:
            return jsonify({"error": "Timeout ao buscar metadata do torrent"}), 504

    torrent_info = handle.get_torrent_info()
    files = torrent_info.files()

    # Pega o maior arquivo (geralmente o vídeo)
    file_index = max(
        range(files.num_files()),
        key=lambda i: files.file_size(i)
    )

    # Prioriza apenas o arquivo de vídeo
    for i in range(files.num_files()):
        handle.file_priority(i, 0)
    handle.file_priority(file_index, 7)  # máxima prioridade

    file_path = f"./downloads/{files.file_path(file_index)}"
    file_size = files.file_size(file_index)

    print(f"Arquivo: {file_path} ({file_size / 1024 / 1024:.1f} MB)")

    def generate():
        print("Aguardando buffer mínimo (5%)...")
        while True:
            s = handle.status()
            progress = s.progress
            print(f"  Progresso: {progress * 100:.1f}% | "
                  f"Download: {s.download_rate / 1024:.1f} KB/s")
            if progress > 0.05:
                break
            time.sleep(1)

        print("Iniciando stream...")
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)  # lê 1MB por vez
                if not chunk:
                    break
                yield chunk

    # Detecta tipo de conteúdo pelo nome do arquivo
    ext = os.path.splitext(file_path)[1].lower()
    content_types = {
        '.mp4':  'video/mp4',
        '.mkv':  'video/x-matroska',
        '.avi':  'video/x-msvideo',
        '.webm': 'video/webm',
        '.mov':  'video/quicktime',
    }
    content_type = content_types.get(ext, 'video/mp4')

    return Response(
        generate(),
        content_type=content_type,
        headers={
            'Content-Length': str(file_size),
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*',
        }
    )


@app.route("/status")
def status():
    """Endpoint para verificar torrents ativos — formato compatível com o radar."""
    torrents = ses.get_torrents()
    result = []
    for h in torrents:
        s = h.status()
        result.append({
            'name': s.name,
            'progress': round(s.progress * 100, 1),       # número, não string
            'progress_str': f"{s.progress * 100:.1f}%",   # string formatada
            'download_rate_kbps': round(s.download_rate / 1024, 1),
            'download_rate_str': f"{s.download_rate / 1024:.1f} KB/s",
            'peers': s.num_peers,
            'state': str(s.state),
        })
    return jsonify({"torrents": result})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)