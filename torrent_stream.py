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
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import json
import re
import queue
import weakref
import mimetypes
import socket
import select
import traceback
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

# ── SUBPROCESS UTILS ─────────────────────────────────────────────────────────
def _win_startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags    |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def _run(cmd: List[str], timeout: int = 15, text: bool = True) -> subprocess.CompletedProcess:
    kwargs: dict = {"capture_output": True, "timeout": timeout}
    if text:
        kwargs["text"]     = True
        kwargs["encoding"] = "utf-8"
        kwargs["errors"]   = "replace"
    si = _win_startupinfo()
    if si:
        try:
            return subprocess.run(cmd, **kwargs, startupinfo=si)
        except OSError as e:
            if hasattr(e, "winerror") and e.winerror == 6:
                print(f"⚠ _run WinError 6 com STARTUPINFO, fallback: {cmd[0]}")
            else:
                raise
    return subprocess.run(cmd, **kwargs)


def _popen(cmd: List[str]) -> subprocess.Popen:
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
    }
    si = _win_startupinfo()
    if si:
        try:
            return subprocess.Popen(cmd, **kwargs, startupinfo=si)
        except OSError as e:
            if hasattr(e, "winerror") and e.winerror == 6:
                print(f"⚠ _popen WinError 6 com STARTUPINFO, fallback: {cmd[0]}")
            else:
                raise
    try:
        return subprocess.Popen(cmd, **kwargs)
    except OSError:
        print(f"⚠ _popen PIPE falhou, abrindo sem stderr: {cmd[0]}")
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
HLS_CACHE_PATH   = ""

BUFFER_READY_BYTES = 3 * 1024 * 1024   # 3 MB
BUFFER_READY_TIMEOUT_S = 120           # 2 min máximo de espera

_hls_start_locks: Dict[str, threading.Lock] = {}
_hls_start_locks_meta = threading.Lock()

def _get_hls_lock(info_hash: str) -> threading.Lock:
    with _hls_start_locks_meta:
        if info_hash not in _hls_start_locks:
            _hls_start_locks[info_hash] = threading.Lock()
        return _hls_start_locks[info_hash]

_gpu_encoder: Optional[str] = None
_gpu_lock = threading.Lock()

# ── FLASK + SESSION ─────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY  = True

active_streams: Dict[str, Any] = {}
active_streams_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════════════
# ── DLNA / UPnP ENGINE (dlnap integrado) ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

SSDP_GROUP = ("239.255.255.250", 1900)
URN_AVTransport = "urn:schemas-upnp-org:service:AVTransport:1"
URN_AVTransport_Fmt = "urn:schemas-upnp-org:service:AVTransport:{}"
URN_RenderingControl = "urn:schemas-upnp-org:service:RenderingControl:1"
URN_RenderingControl_Fmt = "urn:schemas-upnp-org:service:RenderingControl:{}"
SSDP_ALL = "ssdp:all"

py3 = sys.version_info[0] == 3
if py3:
    from urllib.request import urlopen
else:
    from urllib2 import urlopen  # type: ignore


def _dlna_get_tag_value(x, i=0):
    x = x.strip()
    value = ''
    tag = ''
    if x[i:].startswith('<?'):
        i += 2
        while i < len(x) and x[i] != '<':
            i += 1
    if x[i:].startswith('</'):
        i += 2
        in_attr = False
        while i < len(x) and x[i] != '>':
            if x[i] == ' ':
                in_attr = True
            if not in_attr:
                tag += x[i]
            i += 1
        return (tag.strip(), '', x[i+1:])
    if not x[i:].startswith('<'):
        return ('', x[i:], '')
    i += 1
    in_attr = False
    while i < len(x) and x[i] != '>':
        if x[i] == ' ':
            in_attr = True
        if not in_attr:
            tag += x[i]
        i += 1
    i += 1
    empty_elmt  = '<' + tag + ' />'
    closed_elmt = '<' + tag + '>None</' + tag + '>'
    if x.startswith(empty_elmt):
        x = x.replace(empty_elmt, closed_elmt)
    while i < len(x):
        value += x[i]
        if x[i] == '>' and value.endswith('</' + tag + '>'):
            close_tag_len = len(tag) + 2
            value = value[:-close_tag_len]
            break
        i += 1
    return (tag.strip(), value[:-1], x[i+1:])


def _dlna_xml2dict(s, ignoreUntilXML=False):
    if ignoreUntilXML:
        s = ''.join(re.findall(".*?(<.*)", s, re.M))
    d = {}
    while s:
        tag, value, s = _dlna_get_tag_value(s)
        value = value.strip()
        isXml, dummy, dummy2 = _dlna_get_tag_value(value)
        if tag not in d:
            d[tag] = []
        if not isXml:
            if not value:
                continue
            d[tag].append(value.strip())
        else:
            d[tag].append(_dlna_xml2dict(value))
    return d


def _dlna_xpath(d, path):
    for p in path.split('/'):
        tag_attr = p.split('@')
        tag = tag_attr[0]
        if tag not in d:
            return None
        attr = tag_attr[1] if len(tag_attr) > 1 else ''
        if attr:
            a, aval = attr.split('=')
            for s in d[tag]:
                if s[a] == [aval]:
                    d = s
                    break
        else:
            d = d[tag][0]
    return d


def _dlna_unescape_xml(xml):
    return xml.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')


def _dlna_get_location_url(raw):
    t = re.findall('\n(?i)location:\s*(.*)\r\s*', raw, re.M)
    return t[0] if t else ''


def _dlna_get_port(location):
    port = re.findall(r'http://.*?:(\d+).*', location)
    return int(port[0]) if port else 80


def _dlna_get_friendly_name(xml):
    name = _dlna_xpath(xml, 'root/device/friendlyName')
    return name if name is not None else 'Unknown'


def _dlna_get_control_url(xml, urn):
    return _dlna_xpath(xml, 'root/device/serviceList/service@serviceType={}/controlURL'.format(urn))


@contextmanager
def _dlna_send_udp(to, packet):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.sendto(packet.encode(), to)
    yield sock
    sock.close()


def _dlna_send_tcp(to, payload):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(to)
        sock.sendall(payload.encode('utf-8'))
        data = sock.recv(2048)
        if py3:
            data = data.decode('utf-8')
        data = _dlna_xml2dict(_dlna_unescape_xml(data), True)
        errorDescription = _dlna_xpath(data, 's:Envelope/s:Body/s:Fault/detail/UPnPError/errorDescription')
        if errorDescription is not None:
            print(f"[DLNA] Error: {errorDescription}")
    except Exception:
        data = ''
    finally:
        sock.close()
    return data


class DlnapDevice:
    """Representa um dispositivo DLNA/UPnP na rede."""

    def __init__(self, raw=None, ip=None):
        self.ip = ip
        self.ssdp_version = 1
        self.port = None
        self.name = 'Unknown'
        self.location = ''
        self.control_url = None
        self.rendering_control_url = None
        self.has_av_transport = False

        if raw is None and ip is not None:
            # Instância direta por IP (sem descoberta)
            return

        try:
            self.__raw = raw.decode() if isinstance(raw, bytes) else raw
            self.location = _dlna_get_location_url(self.__raw)
            self.port = _dlna_get_port(self.location)

            raw_desc_xml = urlopen(self.location).read().decode()
            self.__desc_xml = _dlna_xml2dict(raw_desc_xml)

            self.name = _dlna_get_friendly_name(self.__desc_xml)
            self.control_url = _dlna_get_control_url(self.__desc_xml, URN_AVTransport)
            self.rendering_control_url = _dlna_get_control_url(self.__desc_xml, URN_RenderingControl)
            self.has_av_transport = self.control_url is not None
        except Exception as e:
            print(f"⚠ DlnapDevice init error (ip={ip}): {e}")

    def __repr__(self):
        return '{} @ {}'.format(self.name, self.ip)

    def __eq__(self, d):
        return self.name == d.name and self.ip == d.ip

    def _payload_from_template(self, action, data, urn):
        fields = ''
        for tag, value in data.items():
            fields += '<{tag}>{value}</{tag}>'.format(tag=tag, value=value)
        payload = """<?xml version="1.0" encoding="utf-8"?>
         <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
            <s:Body>
               <u:{action} xmlns:u="{urn}">
                  {fields}
               </u:{action}>
            </s:Body>
         </s:Envelope>""".format(action=action, urn=urn, fields=fields)
        return payload

    def _create_packet(self, action, data):
        if action in ["SetVolume", "SetMute", "GetVolume"]:
            url = self.rendering_control_url
            urn = URN_RenderingControl_Fmt.format(self.ssdp_version)
        else:
            url = self.control_url
            urn = URN_AVTransport_Fmt.format(self.ssdp_version)
        payload = self._payload_from_template(action=action, data=data, urn=urn)
        packet = "\r\n".join([
            'POST {} HTTP/1.1'.format(url),
            'User-Agent: TorrentStream/3.2.0',
            'Accept: */*',
            'Content-Type: text/xml; charset="utf-8"',
            'HOST: {}:{}'.format(self.ip, self.port),
            'Content-Length: {}'.format(len(payload)),
            'SOAPACTION: "{}#{}"'.format(urn, action),
            'Connection: close',
            '',
            payload,
        ])
        return packet

    def set_current_media(self, url, instance_id=0):
        packet = self._create_packet('SetAVTransportURI', {
            'InstanceID': instance_id, 'CurrentURI': url, 'CurrentURIMetaData': ''
        })
        _dlna_send_tcp((self.ip, self.port), packet)

    def play(self, instance_id=0):
        packet = self._create_packet('Play', {'InstanceID': instance_id, 'Speed': 1})
        _dlna_send_tcp((self.ip, self.port), packet)

    def pause(self, instance_id=0):
        packet = self._create_packet('Pause', {'InstanceID': instance_id, 'Speed': 1})
        _dlna_send_tcp((self.ip, self.port), packet)

    def stop(self, instance_id=0):
        packet = self._create_packet('Stop', {'InstanceID': instance_id, 'Speed': 1})
        _dlna_send_tcp((self.ip, self.port), packet)

    def seek(self, position, instance_id=0):
        packet = self._create_packet('Seek', {
            'InstanceID': instance_id, 'Unit': 'REL_TIME', 'Target': position
        })
        _dlna_send_tcp((self.ip, self.port), packet)

    def volume(self, volume=10, instance_id=0):
        packet = self._create_packet('SetVolume', {
            'InstanceID': instance_id, 'DesiredVolume': volume, 'Channel': 'Master'
        })
        _dlna_send_tcp((self.ip, self.port), packet)

    def mute(self, instance_id=0):
        packet = self._create_packet('SetMute', {
            'InstanceID': instance_id, 'DesiredMute': '1', 'Channel': 'Master'
        })
        _dlna_send_tcp((self.ip, self.port), packet)

    def unmute(self, instance_id=0):
        packet = self._create_packet('SetMute', {
            'InstanceID': instance_id, 'DesiredMute': '0', 'Channel': 'Master'
        })
        _dlna_send_tcp((self.ip, self.port), packet)

    def info(self, instance_id=0):
        packet = self._create_packet('GetTransportInfo', {'InstanceID': instance_id})
        return _dlna_send_tcp((self.ip, self.port), packet)

    def media_info(self, instance_id=0):
        packet = self._create_packet('GetMediaInfo', {'InstanceID': instance_id})
        return _dlna_send_tcp((self.ip, self.port), packet)

    def position_info(self, instance_id=0):
        packet = self._create_packet('GetPositionInfo', {'InstanceID': instance_id})
        return _dlna_send_tcp((self.ip, self.port), packet)


def dlna_discover(name='', ip='', timeout=5, st=SSDP_ALL, mx=3, ssdp_version=1) -> List[DlnapDevice]:
    """Descobre dispositivos UPnP/DLNA na rede local."""
    st_val = st.format(ssdp_version) if '{}' in st else st
    payload = "\r\n".join([
        'M-SEARCH * HTTP/1.1',
        'User-Agent: TorrentStream/3.2.0',
        'HOST: {}:{}'.format(*SSDP_GROUP),
        'Accept: */*',
        'MAN: "ssdp:discover"',
        'ST: {}'.format(st_val),
        'MX: {}'.format(mx),
        '',
        ''
    ])
    devices = []
    with _dlna_send_udp(SSDP_GROUP, payload) as sock:
        start = time.time()
        while True:
            if time.time() - start > timeout:
                break
            r, w, x = select.select([sock], [], [sock], 1)
            if sock in r:
                data, addr = sock.recvfrom(1024)
                if ip and addr[0] != ip:
                    continue
                d = DlnapDevice(data, addr[0])
                d.ssdp_version = ssdp_version
                if d not in devices:
                    if not name or name.lower() in d.name.lower():
                        devices.append(d)
                        if ip and d.has_av_transport:
                            break
    return devices


# ── CAST MANAGER ──────────────────────────────────────────────────────────────
class CastManager:
    """Gerencia descoberta e controle de dispositivos DLNA/UPnP (Smart TVs)."""

    def __init__(self):
        self.devices: List[DlnapDevice] = []
        self._lock = threading.Lock()
        self._last_discovery: float = 0

    def discover_devices(self, force: bool = False) -> List[Dict[str, str]]:
        """Descobre dispositivos DLNA na rede. Cache de 30 segundos."""
        now = time.time()
        if not force and (now - self._last_discovery < 30) and self.devices:
            return self._get_device_list()

        print("🔍 Buscando dispositivos DLNA na rede...")
        try:
            found = dlna_discover(timeout=5)
            with self._lock:
                self.devices = found
                self._last_discovery = now
            print(f"📺 {len(found)} dispositivo(s) DLNA encontrado(s)")
        except Exception as e:
            print(f"⚠ Erro na descoberta DLNA: {e}")

        return self._get_device_list()

    def _get_device_list(self) -> List[Dict[str, str]]:
        return [
            {
                "name":     d.name,
                "ip":       d.ip,
                "location": d.location or "",
                "has_av_transport": d.has_av_transport,
            }
            for d in self.devices
        ]

    def _get_device(self, device_ip: str) -> Optional[DlnapDevice]:
        """Retorna dispositivo do cache ou tenta descoberta direta por IP."""
        with self._lock:
            for d in self.devices:
                if d.ip == device_ip:
                    return d
        # Tenta descoberta direta por IP (dispositivo não estava no cache)
        try:
            found = dlna_discover(ip=device_ip, timeout=10)
            if found:
                with self._lock:
                    self.devices.append(found[0])
                return found[0]
        except Exception as e:
            print(f"⚠ Descoberta direta falhou para {device_ip}: {e}")
        return None

    def play_on_device(self, device_ip: str, url: str) -> bool:
        """Envia URL de vídeo para um dispositivo DLNA e inicia reprodução."""
        target = self._get_device(device_ip)
        if not target:
            print(f"❌ Dispositivo {device_ip} não encontrado")
            return False

        print(f"📺 Enviando para {target.name} ({device_ip}): {url[:60]}...")
        try:
            target.stop()
            time.sleep(0.5)
            target.set_current_media(url)
            time.sleep(0.3)
            target.play()
            print(f"✅ Reprodução iniciada em {target.name}")
            return True
        except Exception as e:
            print(f"❌ Erro ao enviar para {target.name}: {e}")
            return False

    def stop_device(self, device_ip: str) -> bool:
        """Para a reprodução no dispositivo."""
        target = self._get_device(device_ip)
        if not target:
            return False
        try:
            target.stop()
            return True
        except Exception as e:
            print(f"⚠ Erro ao parar {device_ip}: {e}")
            return False

    def pause_device(self, device_ip: str) -> bool:
        target = self._get_device(device_ip)
        if not target:
            return False
        try:
            target.pause()
            return True
        except Exception:
            return False

    def set_volume(self, device_ip: str, volume: int) -> bool:
        target = self._get_device(device_ip)
        if not target:
            return False
        try:
            target.volume(volume)
            return True
        except Exception:
            return False


# Instância global do CastManager
cast_manager = CastManager()

# ══════════════════════════════════════════════════════════════════════════════
# ── SSE (Server-Sent Events) ENGINE ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

# Filas globais e por info_hash usando WeakSet para auto-limpeza
_sse_global_queues: "weakref.WeakSet[queue.Queue]" = weakref.WeakSet()
_sse_global_queues_lock = threading.Lock()
_sse_hash_queues: Dict[str, "weakref.WeakSet[queue.Queue]"] = {}
_sse_hash_queues_lock = threading.Lock()


def _sse_format(event: str, data: Any) -> str:
    """Formata uma mensagem SSE."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _sse_broadcast_global(event: str, data: Any) -> None:
    """Envia evento para todos os clientes SSE globais."""
    msg = _sse_format(event, data)
    with _sse_global_queues_lock:
        for q in list(_sse_global_queues):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


def _sse_broadcast_hash(info_hash: str, event: str, data: Any) -> None:
    """Envia evento para os clientes SSE de um torrent específico."""
    msg = _sse_format(event, data)
    with _sse_hash_queues_lock:
        qs = _sse_hash_queues.get(info_hash)
        if qs:
            for q in list(qs):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


def _build_torrent_snapshot(h: lt.torrent_handle) -> dict:
    """Constrói um snapshot do estado atual de um torrent para SSE."""
    s  = h.status()
    ih = str(s.info_hash).lower()

    with active_streams_lock:
        entry = active_streams.get(ih, {})

    file_path = entry.get("file_path", "")
    file_size = entry.get("file_size", 0)

    buffer_health = 0
    for m in ("audio", "full", "copy"):
        d = os.path.join(HLS_CACHE_PATH, f"{ih[:16]}_{m}")
        if os.path.isdir(d):
            segs = len([f for f in os.listdir(d) if f.endswith(".ts")])
            buffer_health = segs * HLS_SEGMENT_SECS
            break

    is_header_ready = False
    if file_path and os.path.exists(file_path):
        try:
            on_disk = os.path.getsize(file_path)
            is_header_ready = on_disk >= min(5 * 1024 * 1024, max(1, int(file_size * 0.02)))
        except OSError:
            pass

    procs = entry.get("ffmpeg_procs", [])
    transcode_running = any(p.poll() is None for p in procs)

    return {
        "info_hash":          ih,
        "name":               s.name,
        "progress":           round(s.progress * 100, 2),
        "download_rate":      round(s.download_rate / 1024, 1),
        "upload_rate":        round(s.upload_rate / 1024, 1),
        "peers":              s.num_peers,
        "state":              str(s.state),
        "is_header_ready":    is_header_ready,
        "buffer_health":      buffer_health,
        "transcode_running":  transcode_running,
        "stream_url":         f"/stream/{ih}" if ih in active_streams else None,
        "hls_url":            f"/hls/{ih}/index.m3u8" if ih in active_streams else None,
    }


def _start_sse_ticker() -> None:
    """
    Inicia o ticker SSE em background.
    Emite eventos a cada 2s para todos os clientes conectados.
    Emite 'global_status' para o canal global e 'progress' para canais individuais.
    """
    if getattr(_start_sse_ticker, "_started", False):
        return
    _start_sse_ticker._started = True  # type: ignore

    def _ticker():
        while True:
            time.sleep(2)
            try:
                torrents = ses.get_torrents()
                if not torrents:
                    continue

                snapshots = [_build_torrent_snapshot(h) for h in torrents]

                # Evento global com todos os torrents
                _sse_broadcast_global("global_status", {
                    "torrents":   snapshots,
                    "timestamp":  time.time(),
                })

                # Evento individual por info_hash
                for snap in snapshots:
                    ih = snap["info_hash"]
                    _sse_broadcast_hash(ih, "progress", snap)

            except Exception as e:
                print(f"⚠ SSE ticker error: {e}")

    threading.Thread(target=_ticker, daemon=True).start()
    print("📡 SSE ticker iniciado")


# ══════════════════════════════════════════════════════════════════════════════
# ── CONFIG FILE ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def load_download_path() -> str:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            p = f.read().strip()
            if os.path.exists(p):
                return p
    return os.path.join(os.environ.get("TEMP", "/tmp"), "TorrentStream")

def save_download_path(path: str) -> None:
    with open(CONFIG_FILE, "w") as f:
        f.write(path)

# ── CONFIG WINDOW ─────────────────────────────────────────────────────────────
def show_config_window() -> dict:
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

# ── SYSTEM TRAY ──────────────────────────────────────────────────────────────
def run_tray(download_path: str, is_temporary: bool, stop_event: threading.Event) -> None:
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

# ── CLEANUP ──────────────────────────────────────────────────────────────────
def cleanup_torrent(handle, file_path: str) -> None:
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

def cleanup_all() -> None:
    if IS_TEMPORARY and os.path.exists(DOWNLOAD_PATH):
        shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
        print(f"🗑 Pasta temporária deletada: {DOWNLOAD_PATH}")

# ── BUFFER WAIT ──────────────────────────────────────────────────────────────
def wait_for_buffer(file_path: str,
                    min_bytes: int = BUFFER_READY_BYTES,
                    timeout_s: int = BUFFER_READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    logged_waiting = False

    while time.monotonic() < deadline:
        if os.path.exists(file_path):
            try:
                on_disk = os.path.getsize(file_path)
            except OSError:
                on_disk = 0

            if on_disk >= min_bytes:
                print(f"✅ Buffer pronto: {on_disk / 1024 / 1024:.1f} MB → {os.path.basename(file_path)}")
                return True

            if not logged_waiting:
                print(f"⏳ Aguardando buffer mínimo ({min_bytes // 1024 // 1024} MB)…")
                logged_waiting = True

        time.sleep(0.5)

    print(f"⚠ wait_for_buffer: timeout após {timeout_s}s — {file_path}")
    return False

# ── FFPROBE: TRACK INFO ──────────────────────────────────────────────────────
def get_track_info(file_path: str) -> dict:
    result: dict = {"audio_tracks": [], "subtitle_tracks": [], "ffprobe_available": False}
    try:
        proc = _run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path],
            timeout=15,
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
                ch_label = {1: "Mono", 2: "Stereo", 6: "5.1 Surround", 8: "7.1 Surround"}.get(
                    ch, f"{ch}ch" if ch else s.get("channel_layout", "")
                )
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
                    "is_default":     s.get("disposition", {}).get("default", 0) == 1,
                    "is_forced":      s.get("disposition", {}).get("forced",  0) == 1,
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
        print("⚠ ffprobe não encontrado — instale FFmpeg e adicione ao PATH")
    except Exception as e:
        print(f"ffprobe error: {e}")

    return result

# ── RANGE-AWARE STREAMING ────────────────────────────────────────────────────
def stream_file_response(file_path: str, file_size: int, content_type: str) -> Response:
    range_header = request.headers.get("Range")

    base_headers = {
        "Accept-Ranges":                 "bytes",
        "Content-Type":                  content_type,
        "Access-Control-Allow-Origin":   "*",
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
    params = {"save_path": DOWNLOAD_PATH, "storage_mode": lt.storage_mode_t(2)}
    handle = lt.add_magnet_uri(ses, magnet, params)

    for _ in range(60):
        if handle.has_metadata():
            break
        time.sleep(1)
    else:
        raise RuntimeError("Timeout ao buscar metadata do torrent")

    ti    = handle.get_torrent_info()
    files = ti.files()

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

    try:
        piece_size    = ti.piece_length()
        header_bytes  = min(5 * 1024 * 1024, file_size // 20)
        header_pieces = max(1, header_bytes // piece_size)
        tail_bytes    = min(1 * 1024 * 1024, file_size // 50)
        tail_pieces   = max(1, tail_bytes // piece_size)
        total_pieces  = ti.num_pieces()

        for piece in range(min(header_pieces, total_pieces)):
            handle.piece_priority(piece, 7)
        for piece in range(max(0, total_pieces - tail_pieces), total_pieces):
            handle.piece_priority(piece, 6)

        print(f"⚡ Fast-start: {header_pieces} peças iniciais + {tail_pieces} finais priorizadas")
    except Exception as e:
        print(f"⚠ Fast-start priority: {e}")

    # Notifica SSE que um novo torrent foi adicionado
    _sse_broadcast_global("torrent_added", {
        "info_hash":    info_hash,
        "name":         handle.status().name,
        "file_size_mb": round(file_size / 1024 / 1024, 1),
        "content_type": content_type,
    })

    return handle, info_hash, file_path, file_size, content_type

# ── HELPERS ──────────────────────────────────────────────────────────────────
def _extract_quality(title: str) -> str:
    m = re.search(r"(4K|2160p|1080p|720p|480p)", title or "", re.IGNORECASE)
    return m.group(1).upper() if m else "SD"

def _extract_size(title: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB|MiB|GiB))", title or "", re.IGNORECASE)
    return m.group(1) if m else ""

def _deduplicate(streams: List[dict]) -> List[dict]:
    seen, unique = set(), []
    for s in streams:
        ih = s.get("infoHash")
        if ih and ih not in seen:
            seen.add(ih)
            unique.append(s)
    return unique

def _sort_streams(streams: List[dict]) -> List[dict]:
    return sorted(streams, key=lambda s: QUALITY_ORDER.get(s.get("quality", "SD"), 4))

# ── NYAA ENGINE ──────────────────────────────────────────────────────────────
def _nyaa_detect_type(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["dual audio", "dual-audio", "dub", "dubbed", "pt-br", "ptbr"]):
        return "DUB"
    if any(x in n for x in ["legendado", "leg.", "[pt]", "portuguese"]):
        return "DUB"
    if any(x in n for x in ["sub", "subtitled", "english-translated", "horriblesubs",
                              "subsplease", "erai-raws"]):
        return "SUB"
    if any(x in n for x in ["raw", "uncensored"]):
        return "RAW"
    return "SUB"

def search_nyaa(keyword: str, episode: Optional[int] = None,
                season: Optional[int] = None,
                trusted_only: bool = False) -> List[dict]:
    try:
        from nyaapy.nyaasi.nyaa import Nyaa
    except ImportError:
        print("⚠ NyaaPy não instalado — pip install nyaapy")
        return []

    try:
        query = keyword.strip()
        if season and season > 1:
            query += f" S{season:02d}"
        if episode:
            if season and season > 1:
                query += f"E{episode:02d}"
            else:
                query += f" - {episode:02d}"

        filters = 2 if trusted_only else 0
        print(f"🔍 Nyaa search: '{query}' filters={filters}")

        results = Nyaa.search(keyword=query, category=NYAA_CAT_ANIME, filters=filters)

        streams = []
        for r in results:
            name    = r.name if hasattr(r, "name") else str(r.get("name", ""))
            magnet  = r.magnet if hasattr(r, "magnet") else r.get("magnet", "")
            size    = r.size if hasattr(r, "size") else r.get("size", "")
            seeders = r.seeders if hasattr(r, "seeders") else r.get("seeders", "0")

            if not magnet:
                continue

            ih_match = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
            if not ih_match:
                continue
            ih = ih_match.group(1).lower()

            quality  = _extract_quality(name)
            dub_type = _nyaa_detect_type(name)

            streams.append({
                "title":    name,
                "infoHash": ih,
                "magnet":   magnet,
                "source":   "nyaa.si",
                "quality":  quality,
                "size":     size,
                "seeders":  int(seeders) if str(seeders).isdigit() else 0,
                "dub_type": dub_type,
                "fileIdx":  None,
            })

        streams.sort(key=lambda s: (-QUALITY_ORDER.get(s["quality"], 4), -s.get("seeders", 0)))
        print(f"🌸 Nyaa: {len(streams)} resultados para '{query}'")
        return streams

    except Exception as e:
        print(f"❗ Nyaa search error: {e}")
        return []

# ── STREMIO ADDON ENGINE ─────────────────────────────────────────────────────
def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    base_url   = addon_url.rstrip("/").replace("/manifest.json", "")
    target_url = f"{base_url}/stream/{media_type}/{media_id}.json"

    try:
        r = http_requests.get(target_url, headers=ADDON_HEADERS, timeout=12)
        if r.status_code == 200:
            try:
                streams = r.json().get("streams", [])
                for s in streams:
                    s["_source"] = addon_url
                return streams
            except ValueError:
                print(f"❌ JSON inválido de {addon_url}")
        else:
            print(f"⚠ {addon_url} retornou {r.status_code}")
    except Exception as e:
        print(f"❗ Falha em {addon_url}: {e}")

    return []

def _normalize_stremio_stream(s: dict) -> dict:
    title = s.get("title", "") or ""
    return {
        "title":    title,
        "infoHash": s.get("infoHash"),
        "magnet":   f"magnet:?xt=urn:btih:{s['infoHash']}" if s.get("infoHash") else None,
        "source":   s.get("_source", ""),
        "fileIdx":  s.get("fileIdx"),
        "quality":  _extract_quality(title),
        "size":     _extract_size(title),
        "seeders":  0,
        "dub_type": _nyaa_detect_type(title),
    }

# ── ID RESOLVERS ─────────────────────────────────────────────────────────────
def resolve_imdb_id(anime_name: str) -> Optional[str]:
    try:
        url = f"https://v3-cinemeta.strem.io/catalog/series/top/search={http_requests.utils.quote(anime_name)}.json"
        r = http_requests.get(url, timeout=8)
        if r.status_code == 200:
            metas = r.json().get("metas", [])
            if metas:
                iid = metas[0].get("id")
                print(f"✅ IMDB ID: {iid} para '{anime_name}'")
                return iid
    except Exception as e:
        print(f"Cinemeta resolve error: {e}")
    return None

def resolve_kitsu_id(anime_name: str) -> Optional[str]:
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
                kid = data[0]["id"]
                print(f"✅ Kitsu ID: {kid} para '{anime_name}'")
                return kid
    except Exception as e:
        print(f"Kitsu resolve error: {e}")
    return None

def build_stremio_ids(imdb_id: Optional[str], kitsu_id: Optional[str],
                      season: int, episode: int) -> List[str]:
    ids = []
    if imdb_id:
        ids.append(f"{imdb_id}:{season}:{episode}")
        if season > 1:
            ids.append(f"{imdb_id}:1:{episode}")
    if kitsu_id:
        ids.append(f"kitsu:{kitsu_id}:{season}:{episode}")
        if season > 1:
            ids.append(f"kitsu:{kitsu_id}:1:{episode}")
    return ids

# ── COMBINED SEARCH ──────────────────────────────────────────────────────────
def search_all_sources(
    name: str,
    season: int,
    episode: int,
    imdb_id: Optional[str],
    kitsu_id: Optional[str],
    use_nyaa: bool = True,
    nyaa_trusted: bool = False,
) -> List[dict]:
    all_streams: List[dict] = []
    futures_map = {}

    with ThreadPoolExecutor(max_workers=16) as ex:

        if imdb_id or kitsu_id:
            ids_to_try = build_stremio_ids(imdb_id, kitsu_id, season, episode)
            for addon in STREMIO_ADDONS:
                for mid in ids_to_try:
                    fut = ex.submit(_fetch_addon_streams, addon, "series", mid)
                    futures_map[fut] = ("stremio", addon)

        if use_nyaa and name:
            fut = ex.submit(search_nyaa, name, episode, season, nyaa_trusted)
            futures_map[fut] = ("nyaa", "nyaa.si")

        for future in as_completed(futures_map):
            source_type, source_name = futures_map[future]
            try:
                batch = future.result()
                if source_type == "stremio":
                    all_streams.extend([_normalize_stremio_stream(s) for s in batch])
                else:
                    all_streams.extend(batch)
            except Exception as e:
                print(f"❗ Erro em {source_name}: {e}")

    unique = _deduplicate(all_streams)
    sorted_streams = _sort_streams(unique)
    print(f"📦 Total final: {len(sorted_streams)} streams únicos")
    return sorted_streams


# ── FFMPEG ERROR ─────────────────────────────────────────────────────────────
class FFmpegError(RuntimeError):
    def __init__(self, code: int, mode: str, video_codec: str,
                 audio_codec: str, encoder: str, detail: str) -> None:
        self.code        = code
        self.mode        = mode
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.encoder     = encoder
        self.detail      = detail
        super().__init__(detail)

    def to_dict(self) -> dict:
        hints = []
        d = self.detail.lower()
        if "ffmpeg" in d and ("not found" in d or "no such file" in d):
            hints.append("FFmpeg não está instalado ou não está no PATH do sistema")
        if "no such stream" in d or "invalid stream" in d:
            hints.append("Stream de vídeo/áudio não encontrado no arquivo")
        if "nvenc" in d or "nvcuda" in d:
            hints.append("NVENC falhou — driver NVIDIA desatualizado ou GPU não suportada")
            hints.append("Tente forçar modo CPU: adicione ?encoder=cpu na URL do HLS")
        if "qsv" in d:
            hints.append("QSV falhou — Intel Media SDK não instalado")
        if "eac3" in d or "ac3" in d or "dts" in d:
            hints.append("Codec de áudio problemático — tente modo 'full' em vez de 'audio'")
        if "moov atom" in d or "invalid data" in d:
            hints.append("Arquivo corrompido ou download incompleto — aguarde mais buffer")
        if "permission" in d:
            hints.append("Permissão negada na pasta de cache — verifique as permissões")
        if not hints:
            hints.append("Verifique se FFmpeg está instalado: ffmpeg -version")

        return {
            "error":       "FFmpeg falhou ao processar o arquivo",
            "ffmpeg_code": self.code,
            "mode":        self.mode,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "encoder":     self.encoder,
            "detail":      self.detail,
            "hints":       hints,
        }


# ── GPU DETECTION ────────────────────────────────────────────────────────────
def detect_gpu_encoder() -> str:
    global _gpu_encoder
    with _gpu_lock:
        if _gpu_encoder is not None:
            return _gpu_encoder

        for encoder, label in [("h264_nvenc", "NVIDIA NVENC"), ("h264_qsv", "Intel QSV")]:
            try:
                proc = _run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "nullsrc=s=64x64:d=1",
                        "-c:v", encoder, "-frames:v", "1", "-f", "null", "-",
                    ],
                    timeout=10, text=False,
                )
                if proc.returncode == 0:
                    print(f"✅ GPU encoder: {label} ({encoder})")
                    _gpu_encoder = encoder
                    return _gpu_encoder
            except FileNotFoundError:
                print("⚠ ffmpeg não encontrado no PATH")
                _gpu_encoder = "libx264"
                return _gpu_encoder
            except Exception as e:
                print(f"⚠ GPU test {encoder}: {e}")

        print("⚠ GPU não detectada — usando libx264 (CPU)")
        _gpu_encoder = "libx264"
        return _gpu_encoder


# ── HLS TRANSCODE ENGINE ──────────────────────────────────────────────────────

def _hls_cache_dir(info_hash: str, mode: str) -> str:
    d = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}")
    os.makedirs(d, exist_ok=True)
    return d


def _is_hls_ready(cache_dir: str) -> bool:
    m3u8 = os.path.join(cache_dir, "index.m3u8")
    return os.path.exists(m3u8) and os.path.getsize(m3u8) > 0


def _probe_streams(file_path: str) -> dict:
    result = {
        "video_codec": "", "audio_codec": "", "audio_channels": 2,
        "audio_tracks_count": 1, "video_pix_fmt": "", "video_profile": "",
    }
    try:
        proc = _run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path],
            timeout=15,
        )
        if proc.returncode != 0:
            return result
        streams = json.loads(proc.stdout).get("streams", [])
        audio_count = 0
        for s in streams:
            ct = s.get("codec_type", "")
            if ct == "video" and not result["video_codec"]:
                result["video_codec"]   = s.get("codec_name", "").lower()
                result["video_pix_fmt"] = s.get("pix_fmt", "").lower()
                result["video_profile"] = s.get("profile", "").lower()
            if ct == "audio":
                audio_count += 1
                if not result["audio_codec"]:
                    result["audio_codec"]    = s.get("codec_name", "").lower()
                    result["audio_channels"] = s.get("channels", 2)
        result["audio_tracks_count"] = max(1, audio_count)
    except Exception as e:
        print(f"_probe_streams error: {e}")
    return result


VIDEO_NEEDS_TRANSCODE = {"hevc", "h265", "av1", "vp9", "mpeg2video", "mpeg4", "wmv3", "vc1"}
AUDIO_NEEDS_TRANSCODE = {
    "eac3", "ac3", "dts", "truehd", "mlp", "flac", "opus", "vorbis",
}


def auto_transcode_mode(file_path: str) -> str:
    info    = _probe_streams(file_path)
    v       = info["video_codec"]
    a       = info["audio_codec"]
    pix_fmt = info.get("video_pix_fmt", "")
    profile = info.get("video_profile", "")
    is_10bit = ("10le" in pix_fmt or "10be" in pix_fmt or
                "hi10p" in profile.lower() or "main 10" in profile.lower())
    print(f"🔍 Probe: video={v!r} pix={pix_fmt!r} profile={profile!r} 10bit={is_10bit}")
    print(f"         audio={a!r} channels={info['audio_channels']}")
    needs_v = v in VIDEO_NEEDS_TRANSCODE or is_10bit
    needs_a = a in AUDIO_NEEDS_TRANSCODE
    mode = "full" if needs_v else ("audio" if needs_a else "copy")
    print(f"🔧 Transcode mode: {mode}")
    return mode


def start_hls_transcode(info_hash: str, file_path: str, mode: str) -> str:
    hls_lock  = _get_hls_lock(info_hash)
    cache_dir = _hls_cache_dir(info_hash, mode)
    m3u8_path = os.path.join(cache_dir, "index.m3u8")

    if _is_hls_ready(cache_dir):
        return m3u8_path

    with hls_lock:
        if _is_hls_ready(cache_dir):
            return m3u8_path

        if not wait_for_buffer(file_path):
            raise FFmpegError(
                code=-2, mode=mode, video_codec="", audio_codec="", encoder="",
                detail=(
                    f"Arquivo ainda não disponível em disco após {BUFFER_READY_TIMEOUT_S}s. "
                    f"Verifique conexão e seeders do torrent."
                ),
            )

        info    = _probe_streams(file_path)
        v_codec = info["video_codec"]
        a_codec = info["audio_codec"]
        encoder = detect_gpu_encoder()

        if mode in ("copy", "audio"):
            video_args = ["-c:v", "copy"]
        elif encoder == "h264_nvenc":
            video_args = [
                "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23",
                "-b:v", "0", "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
            ]
        elif encoder == "h264_qsv":
            video_args = [
                "-c:v", "h264_qsv", "-global_quality", "23", "-look_ahead", "1",
                "-profile:v", "high", "-pix_fmt", "yuv420p",
            ]
        else:
            video_args = [
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
            ]

        if mode == "copy":
            audio_args = ["-c:a", "copy"]
        elif a_codec == "aac" and mode == "audio":
            if info["audio_channels"] > 2:
                audio_args = ["-c:a", "aac", "-ac", "2", "-b:a", "192k", "-ar", "48000"]
            else:
                audio_args = ["-c:a", "copy"]
        else:
            audio_args = [
                "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
                "-af", "aresample=resampler=swr,aformat=channel_layouts=stereo",
            ]

        seg_pattern = os.path.join(cache_dir, "seg%05d.ts")

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts+igndts",
            "-analyzeduration", "10000000", "-probesize", "10000000",
            "-i", os.path.normpath(file_path),
            "-map", "0:v:0", "-map", "0:a:0",
            *video_args, *audio_args,
            "-f", "hls", "-hls_time", str(HLS_SEGMENT_SECS),
            "-hls_list_size", "0",
            "-hls_flags", "independent_segments+append_list",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", seg_pattern,
            "-start_number", "0",
            "-muxdelay", "0", "-muxpreload", "0",
            m3u8_path,
        ]

        print(f"🎬 FFmpeg HLS [{mode}] video:{v_codec}→{'copy' if mode in ('copy','audio') else 'h264'} audio:{a_codec}→{'copy' if mode=='copy' else 'aac'}")

        stderr_lines: List[str] = []
        stderr_lock  = threading.Lock()

        proc = _popen(cmd)

        with active_streams_lock:
            if info_hash in active_streams:
                active_streams[info_hash].setdefault("ffmpeg_procs", []).append(proc)

        def _collect_stderr(p: subprocess.Popen) -> None:
            for raw in (p.stderr or []):
                line = raw.decode(errors="replace").strip()
                if line:
                    with stderr_lock:
                        stderr_lines.append(line)
                    if not any(x in line for x in ("frame=", "size=", "time=", "speed=")):
                        print(f"[ffmpeg/{mode[:4]}] {line}")
            rc = p.wait()
            print(f"[ffmpeg/{mode[:4]}] encerrado rc={rc}")

        threading.Thread(target=_collect_stderr, args=(proc,), daemon=True).start()

        def _get_ffmpeg_error() -> str:
            with stderr_lock:
                lines = list(stderr_lines)
            errors = [l for l in lines if not any(
                x in l for x in ("frame=", "size=", "time=", "speed=", "Past duration")
            )]
            if not errors:
                return "FFmpeg falhou sem mensagem de erro"
            snippet = " | ".join(errors[-5:])
            return snippet[:600]

        seg0 = os.path.join(cache_dir, "seg00000.ts")
        for i in range(90):
            if os.path.exists(seg0) and os.path.getsize(seg0) > 8192:
                print(f"✅ Primeiro segmento HLS pronto ({i*0.5:.1f}s)")
                # Notifica SSE que HLS está pronto
                _sse_broadcast_hash(info_hash, "ready", {
                    "info_hash": info_hash,
                    "hls_url":   f"http://localhost:5000/hls/{info_hash}/index.m3u8",
                    "mode":      mode,
                })
                return m3u8_path

            rc = proc.poll()
            if rc is not None and not os.path.exists(seg0):
                time.sleep(0.2)
                err_msg = _get_ffmpeg_error()
                raise FFmpegError(
                    code=rc, mode=mode, video_codec=v_codec,
                    audio_codec=a_codec, encoder=encoder, detail=err_msg,
                )
            time.sleep(0.5)

        proc.kill()
        raise FFmpegError(
            code=-1, mode=mode, video_codec=v_codec, audio_codec=a_codec,
            encoder=encoder, detail="Timeout: primeiro segmento não gerado em 45s",
        )


def extract_subtitle_vtt(info_hash: str, file_path: str, stream_index: int = 0) -> Optional[str]:
    cache_dir = _hls_cache_dir(info_hash, "subs")
    vtt_path  = os.path.join(cache_dir, f"sub_{stream_index}.vtt")

    if os.path.exists(vtt_path) and os.path.getsize(vtt_path) > 0:
        with open(vtt_path, "r", encoding="utf-8", errors="replace") as fh:
            if fh.read(6).upper().startswith("WEBVTT"):
                return vtt_path
        os.remove(vtt_path)

    try:
        probe = _run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", f"s:{stream_index}", file_path],
            timeout=10,
        )
        streams = json.loads(probe.stdout).get("streams", [])
    except Exception as e:
        print(f"extract_subtitle_vtt probe error: {e}")
        return None

    if not streams:
        return None

    sub_codec = streams[0].get("codec_name", "").lower()
    GRAPHIC_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvbsub", "pgssub"}
    if sub_codec in GRAPHIC_CODECS:
        print(f"⚠ Legenda {stream_index} é formato gráfico ({sub_codec}), sem suporte OCR")
        return None

    def _ffmpeg_extract(extra_args: List[str], output_path: str, timeout: int = 120) -> bool:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", file_path, "-map", f"0:s:{stream_index}",
            *extra_args, output_path,
        ]
        try:
            proc = _run(cmd, timeout=timeout, text=False)
            return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except Exception as e:
            print(f"_ffmpeg_extract error: {e}")
            return False

    def _fix_vtt_timestamps(text: str) -> str:
        text = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
        text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    def _validate_vtt(path: str) -> bool:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(512)
            return content.upper().strip().startswith("WEBVTT") and "-->" in content
        except Exception:
            return False

    success = False

    if sub_codec in ("ass", "ssa"):
        ass_tmp = os.path.join(cache_dir, f"sub_{stream_index}_tmp.ass")
        if _ffmpeg_extract(["-c:s", "copy"], ass_tmp):
            cmd_conv = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", ass_tmp, "-c:s", "webvtt", "-f", "webvtt", vtt_path,
            ]
            try:
                proc = _run(cmd_conv, timeout=60, text=False)
                success = (proc.returncode == 0 and os.path.exists(vtt_path)
                           and os.path.getsize(vtt_path) > 0)
            except Exception as e:
                print(f"ASS→VTT conv error: {e}")
            try:
                os.remove(ass_tmp)
            except OSError:
                pass

    if not success:
        success = _ffmpeg_extract(["-c:s", "webvtt", "-f", "webvtt"], vtt_path)

    if success and os.path.exists(vtt_path):
        with open(vtt_path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        if not raw.strip().upper().startswith("WEBVTT"):
            raw = "WEBVTT\n\n" + raw
        raw = _fix_vtt_timestamps(raw)
        with open(vtt_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        if _validate_vtt(vtt_path):
            print(f"💬 Legenda {stream_index} ({sub_codec}) → {vtt_path}")
            return vtt_path
        else:
            os.remove(vtt_path)

    srt_tmp = os.path.join(cache_dir, f"sub_{stream_index}_fallback.srt")
    if _ffmpeg_extract(["-c:s", "subrip"], srt_tmp):
        try:
            with open(srt_tmp, "r", encoding="utf-8", errors="replace") as fh:
                srt_text = fh.read()
            vtt_text = "WEBVTT\n\n" + _fix_vtt_timestamps(srt_text)
            with open(vtt_path, "w", encoding="utf-8") as fh:
                fh.write(vtt_text)
            if _validate_vtt(vtt_path):
                print(f"💬 Legenda {stream_index} via SRT fallback → {vtt_path}")
                return vtt_path
        except Exception as e:
            print(f"SRT fallback error: {e}")
        finally:
            try:
                os.remove(srt_tmp)
            except OSError:
                pass

    print(f"❌ Todas as rotas falharam para legenda {stream_index} ({sub_codec})")
    return None


def kill_ffmpeg_procs(info_hash: str) -> None:
    with active_streams_lock:
        procs = active_streams.get(info_hash, {}).get("ffmpeg_procs", [])

    for proc in procs:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass

    with active_streams_lock:
        if info_hash in active_streams:
            active_streams[info_hash]["ffmpeg_procs"] = []


# ── MOTOR DE TRADUÇÃO GOOGLE ─────────────────────────────────────────────────
def google_translate_v1(text: str, target_lang: str = "pt") -> str:
    stripped = text.strip()
    if (
        not stripped
        or stripped.isdigit()
        or "-->" in stripped
        or re.match(r"^<[^>]+>$", stripped)
    ):
        return text

    try:
        params = {
            "client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": stripped,
        }
        r = http_requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params=params, timeout=5,
        )
        if r.status_code == 200:
            parts = r.json()[0]
            translated = "".join(p[0] for p in parts if p and p[0])
            return translated if translated else text
    except Exception as e:
        print(f"⚠ google_translate_v1: {e}")

    return text


# ══════════════════════════════════════════════════════════════════════════════
# ── FLASK ROUTES ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/ping")
def ping():
    return jsonify({"status": "online", "version": "3.2.0"})

# ── SSE: /events/global ───────────────────────────────────────────────────────
@app.route("/events/global")
def sse_global():
    """
    Stream de eventos SSE global com estado de todos os torrents.
    O cliente recebe eventos a cada 2s com o snapshot completo.

    Eventos emitidos:
      - global_status: lista de todos os torrents ativos
      - torrent_added: quando um novo torrent é iniciado
      - torrent_removed: quando um torrent é removido
    """
    _start_sse_ticker()

    q: queue.Queue = queue.Queue(maxsize=50)
    with _sse_global_queues_lock:
        _sse_global_queues.add(q)

    def generate():
        # Envia estado inicial imediatamente
        try:
            torrents = ses.get_torrents()
            snapshots = [_build_torrent_snapshot(h) for h in torrents]
            yield _sse_format("global_status", {
                "torrents":  snapshots,
                "timestamp": time.time(),
            })
        except Exception:
            pass

        # Stream contínuo
        while True:
            try:
                msg = q.get(timeout=30)
                yield msg
            except queue.Empty:
                yield ": heartbeat\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── SSE: /events/<info_hash> ──────────────────────────────────────────────────
@app.route("/events/<info_hash>")
def sse_torrent(info_hash):
    """
    Stream de eventos SSE para um torrent específico.

    Eventos emitidos:
      - progress: atualização de progresso a cada 2s
      - ready: quando o primeiro segmento HLS está disponível
      - started: quando o torrent é adicionado à sessão
    """
    info_hash = info_hash.lower()
    _start_sse_ticker()

    q: queue.Queue = queue.Queue(maxsize=50)
    with _sse_hash_queues_lock:
        if info_hash not in _sse_hash_queues:
            _sse_hash_queues[info_hash] = weakref.WeakSet()
        _sse_hash_queues[info_hash].add(q)

    def generate():
        # Envia snapshot imediato se o torrent já existe
        with active_streams_lock:
            entry = active_streams.get(info_hash)
        if entry:
            for h in ses.get_torrents():
                if str(h.status().info_hash).lower() == info_hash:
                    yield _sse_format("progress", _build_torrent_snapshot(h))
                    break

        while True:
            try:
                msg = q.get(timeout=30)
                yield msg
            except queue.Empty:
                yield ": heartbeat\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── CAST: /cast/devices ───────────────────────────────────────────────────────
@app.route("/cast/devices")
def cast_devices():
    """
    Lista todos os dispositivos DLNA/UPnP encontrados na rede local.

    Query params:
      ?force=true   — ignora o cache de 30s e faz nova descoberta

    Resposta:
      { "devices": [{ "name": "Samsung TV", "ip": "192.168.1.15", ... }] }
    """
    force = request.args.get("force", "false").lower() == "true"
    devices = cast_manager.discover_devices(force=force)
    return jsonify({"devices": devices, "count": len(devices)})


# ── CAST: /cast/play ──────────────────────────────────────────────────────────
@app.route("/cast/play", methods=["POST"])
def cast_play():
    """
    Envia uma URL de vídeo para um dispositivo DLNA e inicia a reprodução.

    Body JSON:
      { "ip": "192.168.1.15", "url": "http://192.168.1.10:5000/hls/abc.../index.m3u8" }

    Dica: Para TVs antigas (Tizen 2018), prefira o URL direto /stream/<hash>
    em vez do HLS, pois o player nativo pode não suportar m3u8.
    """
    data       = request.get_json(silent=True) or {}
    device_ip  = data.get("ip", "").strip()
    url        = data.get("url", "").strip()

    if not device_ip or not url:
        return jsonify({"error": "'ip' e 'url' são obrigatórios"}), 400

    # Se a URL é relativa ou usa localhost, tenta substituir pelo IP da interface
    if url.startswith("/") or "localhost" in url or "127.0.0.1" in url:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((device_ip, 80))
            server_ip = s.getsockname()[0]
            s.close()
            url = re.sub(r"https?://(localhost|127\.0\.0\.1)", f"http://{server_ip}", url)
            if url.startswith("/"):
                url = f"http://{server_ip}:5000{url}"
        except Exception:
            pass

    success = cast_manager.play_on_device(device_ip, url)

    if success:
        return jsonify({"success": True, "device_ip": device_ip, "url": url})
    else:
        return jsonify({
            "success": False,
            "error":   f"Não foi possível enviar para {device_ip}",
            "hint":    "Certifique-se de que o dispositivo está na mesma rede e é compatível com DLNA/UPnP",
        }), 502


# ── CAST: /cast/stop ──────────────────────────────────────────────────────────
@app.route("/cast/stop", methods=["POST"])
def cast_stop():
    """Para a reprodução em um dispositivo DLNA."""
    data      = request.get_json(silent=True) or {}
    device_ip = data.get("ip", "").strip()

    if not device_ip:
        return jsonify({"error": "'ip' é obrigatório"}), 400

    success = cast_manager.stop_device(device_ip)
    return jsonify({"success": success, "device_ip": device_ip})


# ── CAST: /cast/pause ─────────────────────────────────────────────────────────
@app.route("/cast/pause", methods=["POST"])
def cast_pause():
    """Pausa a reprodução em um dispositivo DLNA."""
    data      = request.get_json(silent=True) or {}
    device_ip = data.get("ip", "").strip()

    if not device_ip:
        return jsonify({"error": "'ip' é obrigatório"}), 400

    success = cast_manager.pause_device(device_ip)
    return jsonify({"success": success})


# ── CAST: /cast/volume ────────────────────────────────────────────────────────
@app.route("/cast/volume", methods=["POST"])
def cast_volume():
    """Ajusta o volume em um dispositivo DLNA."""
    data      = request.get_json(silent=True) or {}
    device_ip = data.get("ip", "").strip()
    volume    = data.get("volume", 50)

    if not device_ip:
        return jsonify({"error": "'ip' é obrigatório"}), 400

    success = cast_manager.set_volume(device_ip, int(volume))
    return jsonify({"success": success, "volume": volume})


# ── /subtitles/proxy ──────────────────────────────────────────────────────────
_BLOCKED_RESPONSE_HEADERS = {
    "access-control-allow-origin", "access-control-allow-methods",
    "access-control-allow-headers", "access-control-allow-credentials",
    "access-control-expose-headers", "content-security-policy",
    "x-frame-options", "x-content-type-options", "strict-transport-security",
    "transfer-encoding", "connection", "keep-alive", "server", "vary",
}

@app.route("/subtitles/proxy")
def subtitle_proxy():
    target_url = request.args.get("url", "").strip()
    if not target_url:
        return jsonify({"error": "Parâmetro 'url' é obrigatório"}), 400
    if not target_url.startswith(("http://", "https://")):
        return jsonify({"error": "URL deve usar http ou https"}), 400

    req_headers = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection":      "keep-alive",
        "Referer":         "https://web.stremio.com/",
    }

    for h in ("Accept", "Accept-Language", "Range"):
        val = request.headers.get(h)
        if val:
            req_headers[h] = val

    try:
        resp = http_requests.get(target_url, headers=req_headers, timeout=12,
                                  allow_redirects=True, stream=False)
    except http_requests.exceptions.ConnectionError:
        return jsonify({"error": "Não foi possível conectar ao servidor remoto"}), 502
    except http_requests.exceptions.Timeout:
        return jsonify({"error": "Servidor remoto não respondeu em 12s"}), 504
    except Exception as e:
        return jsonify({"error": f"Erro na requisição: {str(e)[:200]}"}), 502

    raw_content_type = resp.headers.get("Content-Type", "").lower()
    body_bytes       = resp.content
    encoding         = resp.encoding or "utf-8"
    try:
        body_text = body_bytes.decode(encoding, errors="replace")
    except Exception:
        body_text = body_bytes.decode("utf-8", errors="replace")

    is_vtt_request = (
        ".vtt" in target_url.lower() or "text/vtt" in raw_content_type
        or "webvtt" in body_text[:20].lower()
    )
    is_srt = (
        ".srt" in target_url.lower() or "application/x-subrip" in raw_content_type
        or (body_text[:200].strip() and body_text[:10].strip().isdigit())
    )

    if is_srt and not body_text.strip().startswith("WEBVTT"):
        body_text = "WEBVTT\n\n" + re.sub(
            r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", body_text
        )
        body_text = re.sub(r"^\d+$", "", body_text, flags=re.MULTILINE)
        final_content_type = "text/vtt; charset=utf-8"
        final_body = body_text.encode("utf-8")
    elif is_vtt_request or "text/vtt" in raw_content_type:
        final_content_type = "text/vtt; charset=utf-8"
        final_body = body_text.encode("utf-8")
    elif "json" in raw_content_type or body_text.strip().startswith(("[", "{")):
        final_content_type = "application/json; charset=utf-8"
        final_body = body_bytes
    else:
        final_content_type = raw_content_type or "text/plain; charset=utf-8"
        final_body = body_bytes

    safe_headers: Dict[str, str] = {}
    for key, val in resp.headers.items():
        if key.lower() not in _BLOCKED_RESPONSE_HEADERS:
            safe_headers[key] = val

    safe_headers["Access-Control-Allow-Origin"]  = "*"
    safe_headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    safe_headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    safe_headers["Content-Type"]   = final_content_type
    safe_headers["Content-Length"] = str(len(final_body))
    safe_headers["Cache-Control"]  = "public, max-age=300"

    return Response(final_body, status=resp.status_code, headers=safe_headers)


@app.route("/subtitles/proxy", methods=["OPTIONS"])
def subtitle_proxy_options():
    return Response("", status=204, headers={
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Range",
        "Access-Control-Max-Age":       "86400",
    })


@app.route("/transcode/test")
def transcode_test():
    issues = []
    ffmpeg_ok   = False
    ffprobe_ok  = False
    ffmpeg_ver  = ""
    gpu_encoder = ""

    try:
        r = _run(["ffmpeg", "-version"], timeout=5)
        if r.returncode == 0:
            ffmpeg_ok  = True
            first_line = r.stdout.splitlines()[0] if r.stdout else ""
            m = re.search(r"ffmpeg version ([\S]+)", first_line)
            ffmpeg_ver = m.group(1) if m else first_line[:40]
        else:
            issues.append("ffmpeg retornou erro — reinstale o FFmpeg")
    except FileNotFoundError:
        issues.append("ffmpeg não encontrado no PATH — instale em ffmpeg.org")
    except Exception as e:
        issues.append(f"ffmpeg error: {e}")

    try:
        r = _run(["ffprobe", "-version"], timeout=5)
        ffprobe_ok = r.returncode == 0
        if not ffprobe_ok:
            issues.append("ffprobe não funciona — reinstale o FFmpeg")
    except FileNotFoundError:
        issues.append("ffprobe não encontrado — verifique se FFmpeg está no PATH")
    except Exception as e:
        issues.append(f"ffprobe error: {e}")

    if ffmpeg_ok:
        gpu_encoder = detect_gpu_encoder()
        if gpu_encoder == "libx264":
            issues.append("GPU não detectada — usando CPU (libx264). Para NVIDIA instale os drivers com suporte a NVENC.")

    gpu_labels = {
        "h264_nvenc": "NVIDIA NVENC (GPU)",
        "h264_qsv":   "Intel QSV (GPU)",
        "libx264":    "CPU (libx264)",
    }

    return jsonify({
        "ffmpeg_ok":      ffmpeg_ok,
        "ffprobe_ok":     ffprobe_ok,
        "ffmpeg_version": ffmpeg_ver,
        "gpu_encoder":    gpu_encoder,
        "gpu_label":      gpu_labels.get(gpu_encoder, gpu_encoder),
        "issues":         issues,
        "ready":          ffmpeg_ok and ffprobe_ok,
    })


@app.route("/addons/search")
def addon_search():
    name         = request.args.get("name", "").strip()
    season       = int(request.args.get("season", 1))
    episode      = int(request.args.get("episode", 1))
    imdb_id      = request.args.get("imdb_id",  "").strip() or None
    kitsu_id     = request.args.get("kitsu_id", "").strip() or None
    use_nyaa     = request.args.get("nyaa", "true").lower() != "false"
    nyaa_trusted = request.args.get("nyaa_trusted", "false").lower() == "true"

    if not name and not imdb_id and not kitsu_id:
        return jsonify({"error": "Forneça 'name', 'imdb_id' ou 'kitsu_id'"}), 400

    if name and (not imdb_id or not kitsu_id):
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_imdb  = ex.submit(resolve_imdb_id,  name) if not imdb_id  else None
            fut_kitsu = ex.submit(resolve_kitsu_id, name) if not kitsu_id else None
            if fut_imdb:
                imdb_id  = fut_imdb.result()
            if fut_kitsu:
                kitsu_id = fut_kitsu.result()

    streams = search_all_sources(
        name=name, season=season, episode=episode,
        imdb_id=imdb_id, kitsu_id=kitsu_id,
        use_nyaa=use_nyaa, nyaa_trusted=nyaa_trusted,
    )

    return jsonify({
        "total":   len(streams),
        "streams": streams,
        "meta": {
            "name": name, "imdb_id": imdb_id, "kitsu_id": kitsu_id,
            "season": season, "episode": episode,
        },
    })


@app.route("/nyaa/search")
def nyaa_search_route():
    q            = request.args.get("q", "").strip()
    episode      = request.args.get("episode", type=int)
    season       = request.args.get("season",  type=int)
    trusted      = request.args.get("trusted", "false").lower() == "true"

    if not q:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400

    streams = search_nyaa(q, episode=episode, season=season, trusted_only=trusted)
    return jsonify({"total": len(streams), "streams": streams})


@app.route("/addons/start", methods=["POST"])
def addon_start():
    data   = request.get_json(silent=True) or {}
    ih     = data.get("infoHash", "").strip().lower()
    magnet = data.get("magnet",   "").strip()
    title  = data.get("title",    "")

    if not ih and not magnet:
        return jsonify({"error": "Forneça 'infoHash' ou 'magnet'"}), 400

    if not magnet and ih:
        magnet = f"magnet:?xt=urn:btih:{ih}"

    try:
        handle, info_hash, file_path, file_size, content_type = bootstrap_magnet(magnet)
    except RuntimeError as e:
        return jsonify({"error": str(e), "stage": "metadata"}), 504

    if not wait_for_buffer(file_path):
        return jsonify({
            "error": "Arquivo não disponível em disco após espera máxima",
            "stage": "buffer",
            "hint":  "Verifique a conexão e o número de seeders do torrent",
        }), 503

    tracks = get_track_info(file_path)
    transcode_mode_detected = auto_transcode_mode(file_path)

    def _lookahead_hls(ih: str, fp: str, mode: str) -> None:
        try:
            start_hls_transcode(ih, fp, mode)
            print(f"🔭 Look-ahead HLS [{mode}] pronto para {ih[:8]}")
        except Exception as e:
            print(f"⚠ Look-ahead error: {e}")

    threading.Thread(
        target=_lookahead_hls,
        args=(info_hash, file_path, transcode_mode_detected),
        daemon=True,
    ).start()

    s = handle.status()

    resp = {
        "info_hash":        info_hash,
        "stream_url":       f"http://localhost:5000/stream/{info_hash}",
        "hls_url":          f"http://localhost:5000/hls/{info_hash}/index.m3u8",
        "transcode_mode":   transcode_mode_detected,
        "name":             s.name,
        "title":            title,
        "file_size_mb":     round(file_size / 1024 / 1024, 1),
        "extension":        os.path.splitext(file_path)[1].lower(),
        "content_type":     content_type,
        "progress":         round(s.progress * 100, 1),
        **tracks,
    }

    with active_streams_lock:
        if info_hash in active_streams:
            active_streams[info_hash]["track_info"] = resp

    # Notifica SSE que o torrent está "started" (com metadados)
    _sse_broadcast_hash(info_hash, "started", {
        "info_hash":  info_hash,
        "name":       s.name,
        "hls_url":    resp["hls_url"],
        "stream_url": resp["stream_url"],
    })

    return jsonify(resp)


@app.route("/stream/<info_hash>")
def stream_by_hash(info_hash):
    info_hash = info_hash.lower()
    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404
    if not os.path.exists(entry["file_path"]):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503
    return stream_file_response(entry["file_path"], entry["file_size"], entry["content_type"])


@app.route("/hls/<info_hash>/index.m3u8")
def hls_playlist(info_hash):
    info_hash = info_hash.lower()
    mode      = request.args.get("mode", "auto").lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503

    if mode == "auto":
        mode = auto_transcode_mode(file_path)

    try:
        m3u8_path = start_hls_transcode(info_hash, file_path, mode)
    except FFmpegError as e:
        err_dict = e.to_dict()
        if e.encoder in ("h264_nvenc", "h264_qsv"):
            global _gpu_encoder
            _gpu_encoder = "libx264"
            shutil.rmtree(_hls_cache_dir(info_hash, mode), ignore_errors=True)
            try:
                m3u8_path = start_hls_transcode(info_hash, file_path, mode)
            except FFmpegError as e2:
                return jsonify(e2.to_dict()), 500
        else:
            return jsonify(err_dict), 500
    except RuntimeError as e:
        return jsonify({"error": str(e), "hints": ["Verifique os logs do servidor"]}), 500

    with open(m3u8_path, "r") as f:
        m3u8_content = f.read()

    m3u8_content = re.sub(
        r"(seg\d+\.ts)",
        lambda m: f"/hls/{info_hash}/{m.group(1)}",
        m3u8_content,
    )

    return Response(
        m3u8_content,
        mimetype="application/vnd.apple.mpegurl",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
    )


@app.route("/hls/<info_hash>/<segment>")
def hls_segment(info_hash, segment):
    info_hash = info_hash.lower()

    if not re.match(r"^seg\d+\.ts$", segment):
        return jsonify({"error": "Segmento inválido"}), 400

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    seg_path = None
    for mode in ("audio", "full", "copy"):
        candidate = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}", segment)
        if os.path.exists(candidate):
            seg_path = candidate
            break

    if not seg_path:
        for _ in range(20):
            for mode in ("audio", "full", "copy"):
                candidate = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}", segment)
                if os.path.exists(candidate):
                    seg_path = candidate
                    break
            if seg_path:
                break
            time.sleep(0.5)

    if not seg_path:
        return jsonify({"error": "Segmento não disponível"}), 503

    file_size = os.path.getsize(seg_path)
    return stream_file_response(seg_path, file_size, "video/mp2t")


@app.route("/subtitles/<info_hash>/<int:sub_index>.vtt")
def serve_subtitle(info_hash, sub_index):
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503

    vtt_path = extract_subtitle_vtt(info_hash, file_path, sub_index)
    if not vtt_path:
        return jsonify({
            "error":  f"Legenda {sub_index} não disponível",
            "reason": "Formato gráfico (PGS/DVB) não suportado, ou falha na extração",
        }), 404

    with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
        vtt_content = f.read()

    return Response(vtt_content, mimetype="text/vtt", headers={
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "max-age=3600",
    })


@app.route("/hls/select-audio/<info_hash>", methods=["POST"])
def hls_select_audio(info_hash):
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    data        = request.get_json(silent=True) or {}
    audio_index = data.get("audio_index", 0)
    mode        = data.get("mode", "auto").lower()

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível"}), 503

    if mode == "auto":
        mode = auto_transcode_mode(file_path)

    kill_ffmpeg_procs(info_hash)
    cache_dir = _hls_cache_dir(info_hash, mode)
    shutil.rmtree(cache_dir, ignore_errors=True)

    with _hls_start_locks_meta:
        _hls_start_locks.pop(info_hash, None)

    with active_streams_lock:
        active_streams[info_hash]["audio_map"] = f"0:a:{audio_index}"

    try:
        m3u8_path = _start_hls_transcode_custom_audio(info_hash, file_path, mode, audio_index)
    except FFmpegError as e:
        return jsonify(e.to_dict()), 500

    hls_url = f"http://localhost:5000/hls/{info_hash}/index.m3u8?mode={mode}"
    return jsonify({"hls_url": hls_url, "audio_index": audio_index, "mode": mode})


def _start_hls_transcode_custom_audio(info_hash: str, file_path: str,
                                       mode: str, audio_index: int) -> str:
    hls_lock  = _get_hls_lock(info_hash)
    cache_dir = _hls_cache_dir(info_hash, mode)
    m3u8_path = os.path.join(cache_dir, "index.m3u8")

    with hls_lock:
        if _is_hls_ready(cache_dir):
            return m3u8_path

        if not wait_for_buffer(file_path):
            raise FFmpegError(-2, mode, "", "", "",
                              "Buffer insuficiente para iniciar transcode com trilha de áudio")

        info    = _probe_streams(file_path)
        v_codec = info["video_codec"]
        a_codec = info["audio_codec"]
        encoder = detect_gpu_encoder()

        if mode in ("copy", "audio"):
            video_args = ["-c:v", "copy"]
        elif encoder == "h264_nvenc":
            video_args = ["-c:v","h264_nvenc","-preset","p4","-rc","vbr","-cq","23",
                          "-b:v","0","-profile:v","high","-level:v","4.1","-pix_fmt","yuv420p"]
        elif encoder == "h264_qsv":
            video_args = ["-c:v","h264_qsv","-global_quality","23","-look_ahead","1",
                          "-profile:v","high","-pix_fmt","yuv420p"]
        else:
            video_args = ["-c:v","libx264","-preset","fast","-crf","23",
                          "-profile:v","high","-level:v","4.1","-pix_fmt","yuv420p"]

        if mode == "copy":
            audio_args = ["-c:a", "copy"]
        elif a_codec == "aac" and mode == "audio" and info["audio_channels"] <= 2:
            audio_args = ["-c:a", "copy"]
        else:
            audio_args = ["-c:a","aac","-b:a","192k","-ac","2","-ar","48000",
                          "-af","aresample=resampler=swr,aformat=channel_layouts=stereo"]

        seg_pattern = os.path.join(cache_dir, "seg%05d.ts")

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts+igndts", "-analyzeduration", "10000000", "-probesize", "10000000",
            "-i", os.path.normpath(file_path),
            "-map", "0:v:0", "-map", f"0:a:{audio_index}",
            *video_args, *audio_args,
            "-f", "hls", "-hls_time", str(HLS_SEGMENT_SECS),
            "-hls_list_size", "0", "-hls_flags", "independent_segments+append_list",
            "-hls_segment_type", "mpegts", "-hls_segment_filename", seg_pattern,
            "-start_number", "0", "-muxdelay", "0", "-muxpreload", "0",
            m3u8_path,
        ]

        stderr_lines: List[str] = []
        stderr_lock  = threading.Lock()
        proc = _popen(cmd)

        with active_streams_lock:
            if info_hash in active_streams:
                active_streams[info_hash].setdefault("ffmpeg_procs", []).append(proc)

        def _collect(p):
            for raw in (p.stderr or []):
                line = raw.decode(errors="replace").strip()
                if line:
                    with stderr_lock:
                        stderr_lines.append(line)
            p.wait()

        threading.Thread(target=_collect, args=(proc,), daemon=True).start()

        seg0 = os.path.join(cache_dir, "seg00000.ts")
        for i in range(90):
            if os.path.exists(seg0) and os.path.getsize(seg0) > 8192:
                return m3u8_path
            rc = proc.poll()
            if rc is not None and not os.path.exists(seg0):
                time.sleep(0.2)
                with stderr_lock:
                    lines = list(stderr_lines)
                errors = [l for l in lines if not any(
                    x in l for x in ("frame=", "size=", "time=", "speed="))]
                detail = " | ".join(errors[-5:])[:600] if errors else "FFmpeg falhou"
                raise FFmpegError(rc, mode, v_codec, a_codec, encoder, detail)
            time.sleep(0.5)

        proc.kill()
        raise FFmpegError(-1, mode, v_codec, a_codec, encoder,
                          "Timeout: primeiro segmento não gerado em 45s")


@app.route("/transcode/status/<info_hash>")
def transcode_status(info_hash):
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash, {})
        procs = entry.get("ffmpeg_procs", [])

    running  = any(p.poll() is None for p in procs)
    segments = 0
    for mode in ("audio", "full", "copy"):
        d = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}")
        if os.path.isdir(d):
            segments = len([f for f in os.listdir(d) if f.endswith(".ts")])
            break

    return jsonify({
        "info_hash":      info_hash,
        "running":        running,
        "segments":       segments,
        "seconds_ready":  segments * HLS_SEGMENT_SECS,
    })


@app.route("/status")
def status():
    result = []
    for h in ses.get_torrents():
        s  = h.status()
        ih = str(s.info_hash).lower()

        with active_streams_lock:
            entry = active_streams.get(ih, {})

        file_path = entry.get("file_path", "")
        file_size = entry.get("file_size", 0)

        is_header_ready = False
        if file_path and os.path.exists(file_path):
            try:
                on_disk = os.path.getsize(file_path)
                is_header_ready = on_disk >= min(5 * 1024 * 1024, max(1, int(file_size * 0.02)))
            except OSError:
                pass

        buffer_health = 0
        for m in ("audio", "full", "copy"):
            d = os.path.join(HLS_CACHE_PATH, f"{ih[:16]}_{m}")
            if os.path.isdir(d):
                segs = len([f for f in os.listdir(d) if f.endswith(".ts")])
                buffer_health = segs * HLS_SEGMENT_SECS
                break

        bitrate_kbps = int((file_size * 8) / 7200 / 1000) if file_size > 0 else 0

        result.append({
            "name":               s.name,
            "info_hash":          ih,
            "progress":           round(s.progress * 100, 1),
            "progress_str":       f"{s.progress * 100:.1f}%",
            "is_header_ready":    is_header_ready,
            "buffer_health":      buffer_health,
            "buffer_health_str":  f"{buffer_health}s prontos",
            "bitrate_estimate":   bitrate_kbps,
            "download_rate_kbps": round(s.download_rate / 1024, 1),
            "upload_rate_kbps":   round(s.upload_rate / 1024, 1),
            "peers":              s.num_peers,
            "state":              str(s.state),
            "stream_url":         f"/stream/{ih}" if ih in active_streams else None,
            "hls_url":            f"/hls/{ih}/index.m3u8" if ih in active_streams else None,
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
        kill_ffmpeg_procs(info_hash)
        with _hls_start_locks_meta:
            _hls_start_locks.pop(info_hash, None)
        with _sse_hash_queues_lock:
            _sse_hash_queues.pop(info_hash, None)
        threading.Thread(
            target=cleanup_torrent,
            args=(entry["handle"], entry["file_path"]),
            daemon=True,
        ).start()

        # Notifica SSE que o torrent foi removido
        _sse_broadcast_global("torrent_removed", {"info_hash": info_hash})

        return jsonify({"success": True})

    for h in ses.get_torrents():
        if str(h.status().info_hash).lower() == info_hash:
            threading.Thread(target=cleanup_torrent, args=(h, ""), daemon=True).start()
            _sse_broadcast_global("torrent_removed", {"info_hash": info_hash})
            return jsonify({"success": True})

    return jsonify({"error": "Torrent não encontrado"}), 404


@app.route("/play")
def play():
    magnet = request.args.get("magnet", "").strip()
    if not magnet:
        return jsonify({"error": "magnet é obrigatório"}), 400
    try:
        handle, _, file_path, file_size, content_type = bootstrap_magnet(magnet)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 504
    wait_for_buffer(file_path)
    return stream_file_response(file_path, file_size, content_type)


@app.route("/search")
def api_search():
    name    = request.args.get("name", "").strip()
    season  = int(request.args.get("season",  1))
    episode = int(request.args.get("episode", 1))

    if not name:
        return jsonify({"error": "Parâmetro 'name' é obrigatório"}), 400

    use_nyaa     = request.args.get("nyaa", "true").lower() != "false"
    nyaa_trusted = request.args.get("nyaa_trusted", "false").lower() == "true"

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_imdb  = ex.submit(resolve_imdb_id,  name)
        fut_kitsu = ex.submit(resolve_kitsu_id, name)
        imdb_id   = fut_imdb.result()
        kitsu_id  = fut_kitsu.result()

    streams = search_all_sources(
        name=name, season=season, episode=episode,
        imdb_id=imdb_id, kitsu_id=kitsu_id,
        use_nyaa=use_nyaa, nyaa_trusted=nyaa_trusted,
    )
    return jsonify({"total": len(streams), "streams": streams})


@app.route("/tracks/<info_hash>")
def api_tracks(info_hash):
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível no disco"}), 503

    cached = entry.get("track_info")
    if cached and cached.get("ffprobe_available"):
        return jsonify({
            "audio_tracks":      cached.get("audio_tracks", []),
            "subtitle_tracks":   cached.get("subtitle_tracks", []),
            "ffprobe_available": True,
        })

    info = get_track_info(file_path)

    with active_streams_lock:
        if info_hash in active_streams and not active_streams[info_hash].get("track_info"):
            active_streams[info_hash]["track_info"] = info

    return jsonify(info)


@app.route("/translate-sub/<info_hash>/<int:track_idx>")
def translate_sub_endpoint(info_hash, track_idx):
    info_hash = info_hash.lower()

    with active_streams_lock:
        entry = active_streams.get(info_hash)
    if not entry:
        return jsonify({"error": "Stream não encontrado"}), 404

    file_path = entry["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Arquivo ainda não disponível no disco"}), 503

    target_lang = request.args.get("lang", "pt").strip().lower()
    use_cache   = request.args.get("cache", "1") != "0"

    cache_dir   = _hls_cache_dir(info_hash, "subs")
    cached_path = os.path.join(cache_dir, f"translated_{track_idx}_{target_lang}.vtt")

    if use_cache and os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
        with open(cached_path, "r", encoding="utf-8") as fh:
            return Response(fh.read(), mimetype="text/vtt", headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "max-age=3600",
            })

    try:
        probe = _run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", f"s:{track_idx}", file_path],
            timeout=10,
        )
        sub_streams = json.loads(probe.stdout).get("streams", [])
    except Exception as e:
        return jsonify({"error": f"ffprobe falhou: {e}"}), 500

    if not sub_streams:
        return jsonify({"error": f"Trilha de legenda {track_idx} não encontrada"}), 404

    sub_codec = sub_streams[0].get("codec_name", "").lower()
    if sub_codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvbsub", "pgssub"):
        return jsonify({
            "error": f"Legenda {track_idx} é formato gráfico ({sub_codec}), não traduzível",
        }), 422

    srt_tmp = os.path.join(cache_dir, f"raw_{track_idx}.srt")
    proc = _run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", file_path, "-map", f"0:s:{track_idx}",
         "-c:s", "subrip", "-y", srt_tmp],
        timeout=120, text=False,
    )

    if proc.returncode != 0 or not os.path.exists(srt_tmp):
        err = (proc.stderr or b"").decode(errors="replace")[:300]
        return jsonify({"error": "Falha ao extrair legenda", "detail": err}), 500

    with open(srt_tmp, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.readlines()

    vtt_lines = ["WEBVTT\n", "\n"]

    for line in raw_lines:
        stripped = line.rstrip("\n")
        if "-->" in stripped:
            vtt_lines.append(
                re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", stripped) + "\n"
            )
        elif stripped.strip().isdigit():
            pass
        elif not stripped.strip():
            vtt_lines.append("\n")
        else:
            translated = google_translate_v1(stripped, target_lang=target_lang)
            vtt_lines.append(translated + "\n")

    vtt_content = "".join(vtt_lines)

    try:
        with open(cached_path, "w", encoding="utf-8") as fh:
            fh.write(vtt_content)
    except OSError as e:
        print(f"⚠ translate-sub: não foi possível salvar cache: {e}")

    try:
        os.remove(srt_tmp)
    except OSError:
        pass

    return Response(vtt_content, mimetype="text/vtt", headers={
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "max-age=3600",
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── MAIN ──────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    result = show_config_window()
    if not result.get("start"):
        sys.exit(0)

    DOWNLOAD_PATH = result["path"]
    IS_TEMPORARY  = result["temporary"]

    HLS_CACHE_PATH = os.path.join(DOWNLOAD_PATH, ".hls_cache")
    os.makedirs(HLS_CACHE_PATH, exist_ok=True)

    print(f"📁 {DOWNLOAD_PATH}  [{'temporário' if IS_TEMPORARY else 'permanente'}]")
    print(f"🎬 HLS cache: {HLS_CACHE_PATH}")
    print("🚀 http://0.0.0.0:5000")
    print()
    print("📡 Rotas SSE disponíveis:")
    print("   GET /events/global          — eventos de todos os torrents")
    print("   GET /events/<info_hash>     — eventos de um torrent específico")
    print()
    print("📺 Rotas DLNA Cast disponíveis:")
    print("   GET  /cast/devices          — listar Smart TVs na rede")
    print("   POST /cast/play             — enviar vídeo para a TV")
    print("   POST /cast/stop             — parar reprodução na TV")
    print("   POST /cast/pause            — pausar reprodução na TV")
    print("   POST /cast/volume           — ajustar volume na TV")

    stop_event = threading.Event()

    threading.Thread(
        target=run_tray,
        args=(DOWNLOAD_PATH, IS_TEMPORARY, stop_event),
        daemon=True,
    ).start()

    # Inicia o SSE ticker automaticamente
    _start_sse_ticker()

    # Faz descoberta DLNA em background ao iniciar
    threading.Thread(
        target=cast_manager.discover_devices,
        daemon=True,
    ).start()

    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, threaded=True),
        daemon=True,
    ).start()

    stop_event.wait()
    cleanup_all()
    sys.exit(0)