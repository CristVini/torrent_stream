import requests
from concurrent.futures import ThreadPoolExecutor

ADDONS = [
    "https://torrentio.strem.fun",
    "https://mediafusion.elfhosted.com",
    "https://comet.elfhosted.com"
]

def fetch_json(url):
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"Erro ao buscar {url}: {e}")
    return None


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

    # remove duplicados
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


# 🔥 FUNÇÃO CENTRAL DE PLAY (CORRIGIDA)
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
                        # espera mais dados (torrent ainda baixando)
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


# ── ROTAS ─────────────────────────────

@app.route("/play")
def play():
    magnet = request.args.get("magnet")
    if not magnet:
        return jsonify({"error": "magnet missing"}), 400

    return play_with_magnet(magnet)


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


@app.route("/addons/play")
def addon_play():
    info_hash = request.args.get("infoHash")

    if not info_hash:
        return jsonify({"error": "infoHash obrigatório"}), 400

    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    return play_with_magnet(magnet)