import os
import sys

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

NYAA_CAT_ANIME    = 1
NYAA_CAT_ANIME_EN = 12
NYAA_CAT_ANIME_RAW = 14

AUDIO_TRANSCODE_CODECS = {
    "eac3", "ac3", "dts", "truehd", "mlp", "flac",
    "dts-hd", "dts-x", "dolby_atmos", "thd",
}

HLS_SEGMENT_SECS = 6
BUFFER_READY_BYTES = 3 * 1024 * 1024   # 3 MB
BUFFER_READY_TIMEOUT_S = 120           # 2 min máximo de espera

# Variáveis globais que serão inicializadas no main
DOWNLOAD_PATH = ""
IS_TEMPORARY  = True
HLS_CACHE_PATH = ""
