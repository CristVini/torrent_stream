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
import zipfile  # Novo: para extrair o FFmpeg baixado
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

# ── FFmpeg AUTO-INSTALLER ────────────────────────────────────────────────────
def ensure_ffmpeg():
    """Verifica se FFmpeg/FFprobe existem, caso contrário, baixa-os (Windows)."""
    # 1. Verifica se já está no PATH do sistema
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True

    # Se não for Windows, não faz download automático (requer gerenciador de pacotes)
    if sys.platform != "win32":
        print("⚠️ FFmpeg não encontrado. Instale-o via 'apt install ffmpeg' ou 'brew install ffmpeg'.")
        return False

    # 2. Caminhos locais
    base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    ffmpeg_dir = os.path.join(base_dir, "ffmpeg_bin")
    bin_path = os.path.join(ffmpeg_dir, "bin")
    
    # Se a pasta já existe, adiciona ao PATH e verifica
    if os.path.exists(bin_path):
        if bin_path not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + bin_path
        if shutil.which("ffmpeg"):
            return True

    print("⚠️ FFmpeg não encontrado! Iniciando download automático...")
    
    try:
        # URL da build estável essentials do gyan.dev
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = os.path.join(base_dir, "ffmpeg.zip")

        # Download com progresso no console
        response = http_requests.get(url, stream=True)
        total_size = int(response.headers.get('content-length', 0))
        
        with open(zip_path, "wb") as f:
            downloaded = 0
            for data in response.iter_content(chunk_size=8192):
                downloaded += len(data)
                f.write(data)
                if total_size > 0:
                    done = int(50 * downloaded / total_size)
                    sys.stdout.write(f"\rBaixando FFmpeg: [{'█' * done}{'.' * (50-done)}] {downloaded/1024/1024:.1f}MB")
                    sys.stdout.flush()
        
        print("\n📦 Extraindo FFmpeg...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Pega o nome da pasta raiz dentro do zip (geralmente ffmpeg-X.X-essentials_build)
            top_folder = zip_ref.namelist()[0].split('/')[0]
            zip_ref.extractall(base_dir)
            
            # Renomeia para um nome fixo
            if os.path.exists(ffmpeg_dir):
                shutil.rmtree(ffmpeg_dir)
            os.rename(os.path.join(base_dir, top_folder), ffmpeg_dir)

        os.remove(zip_path) # Deleta o arquivo zip
        
        # Adiciona ao PATH do processo atual
        if bin_path not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + bin_path
            
        print("✅ FFmpeg instalado com sucesso!")
        return True

    except Exception as e:
        print(f"❌ Falha ao baixar FFmpeg: {e}")
        return False

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
    def __init__(self):
        self.devices: List[DlnapDevice] = []
        self._lock = threading.Lock()
        self._last_discovery: float = 0

    def discover_devices(self, force: bool = False) -> List[Dict[str, str]]:
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
        with self._lock:
            for d in self.devices:
                if d.ip == device_ip:
                    return d
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


cast_manager = CastManager()

# ══════════════════════════════════════════════════════════════════════════════
# ── SSE ENGINE ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_sse_global_queues: "weakref.WeakSet[queue.Queue]" = weakref.WeakSet()
_sse_global_queues_lock = threading.Lock()
_sse_hash_queues: Dict[str, "weakref.WeakSet[queue.Queue]"] = {}
_sse_hash_queues_lock = threading.Lock()


def _sse_format(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _sse_broadcast_global(event: str, data: Any) -> None:
    msg = _sse_format(event, data)
    with _sse_global_queues_lock:
        for q in list(_sse_global_queues):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


def _sse_broadcast_hash(info_hash: str, event: str, data: Any) -> None:
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
                _sse_broadcast_global("global_status", {
                    "torrents":   snapshots,
                    "timestamp":  time.time(),
                })
                for snap in snapshots:
                    ih = snap["info_hash"]
                    _sse_broadcast_hash(ih, "progress", snap)
            except Exception as e:
                print(f"⚠ SSE ticker error: {e}")

    threading.Thread(target=_ticker, daemon=True).start()
    print("📡 SSE ticker iniciado")


# ── CONFIG FILE ──────────────────────────────────────────────────────────────
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
                    if not data: break
                    remaining -= len(data)
                    yield data
        return Response(gen_range(), status=206, headers={
            **base_headers, "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        })
    def gen_full():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk: break
                yield chunk
    return Response(gen_full(), status=200, headers={**base_headers, "Content-Length": str(file_size)})

# ── TORRENT BOOTSTRAP ────────────────────────────────────────────────────────
def bootstrap_magnet(magnet: str):
    params = {"save_path": DOWNLOAD_PATH, "storage_mode": lt.storage_mode_t(2)}
    handle = lt.add_magnet_uri(ses, magnet, params)
    for _ in range(60):
        if handle.has_metadata(): break
        time.sleep(1)
    else: raise RuntimeError("Timeout ao buscar metadata do torrent")
    ti    = handle.get_torrent_info()
    files = ti.files()
    best_idx, best_size = -1, -1
    for i in range(files.num_files()):
        ext  = os.path.splitext(files.file_path(i))[1].lower()
        size = files.file_size(i)
        if ext in VIDEO_EXTS and size > best_size:
            best_size, best_idx = size, i
    if best_idx == -1: best_idx = max(range(files.num_files()), key=lambda i: files.file_size(i))
    for i in range(files.num_files()): handle.file_priority(i, 0)
    handle.file_priority(best_idx, 7)
    file_path    = os.path.join(DOWNLOAD_PATH, files.file_path(best_idx))
    file_size    = files.file_size(best_idx)
    ext          = os.path.splitext(file_path)[1].lower()
    content_type = CONTENT_TYPES.get(ext, "video/mp4")
    info_hash    = str(handle.status().info_hash).lower()
    with active_streams_lock:
        active_streams[info_hash] = {
            "handle": handle, "file_path": file_path, "file_size": file_size,
            "content_type": content_type, "track_info": None,
        }
    print(f"▶ {os.path.basename(file_path)} ({file_size/1024/1024:.1f} MB) hash={info_hash}")
    try:
        piece_size    = ti.piece_length()
        header_bytes  = min(5 * 1024 * 1024, file_size // 20)
        header_pieces = max(1, header_bytes // piece_size)
        tail_bytes    = min(1 * 1024 * 1024, file_size // 50)
        tail_pieces   = max(1, tail_bytes // piece_size)
        total_pieces  = ti.num_pieces()
        for piece in range(min(header_pieces, total_pieces)): handle.piece_priority(piece, 7)
        for piece in range(max(0, total_pieces - tail_pieces), total_pieces): handle.piece_priority(piece, 6)
    except Exception as e: print(f"⚠ Fast-start priority: {e}")
    _sse_broadcast_global("torrent_added", {
        "info_hash": info_hash, "name": handle.status().name,
        "file_size_mb": round(file_size / 1024 / 1024, 1), "content_type": content_type,
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

def _nyaa_detect_type(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["dual audio", "dub", "pt-br"]): return "DUB"
    if any(x in n for x in ["legendado", "leg.", "[pt]"]): return "DUB"
    if any(x in n for x in ["sub", "subtitled"]): return "SUB"
    if any(x in n for x in ["raw", "uncensored"]): return "RAW"
    return "SUB"

# ── SEARCH ENGINES ───────────────────────────────────────────────────────────
def search_nyaa(keyword: str, episode: Optional[int] = None, season: Optional[int] = None, trusted_only: bool = False) -> List[dict]:
    try:
        from nyaapy.nyaasi.nyaa import Nyaa
    except ImportError: return []
    try:
        query = keyword.strip()
        if season and season > 1: query += f" S{season:02d}"
        if episode: query += f" - {episode:02d}"
        results = Nyaa.search(keyword=query, category=NYAA_CAT_ANIME, filters=2 if trusted_only else 0)
        streams = []
        for r in results:
            name    = r.name if hasattr(r, "name") else str(r.get("name", ""))
            magnet  = r.magnet if hasattr(r, "magnet") else r.get("magnet", "")
            if not magnet: continue
            ih_match = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
            if not ih_match: continue
            streams.append({
                "title": name, "infoHash": ih_match.group(1).lower(), "magnet": magnet,
                "source": "nyaa.si", "quality": _extract_quality(name), "size": r.size if hasattr(r, "size") else "",
                "seeders": int(r.seeders) if hasattr(r, "seeders") and str(r.seeders).isdigit() else 0,
                "dub_type": _nyaa_detect_type(name), "fileIdx": None,
            })
        return streams
    except: return []

def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    base_url   = addon_url.rstrip("/").replace("/manifest.json", "")
    target_url = f"{base_url}/stream/{media_type}/{media_id}.json"
    try:
        r = http_requests.get(target_url, headers=ADDON_HEADERS, timeout=12)
        if r.status_code == 200:
            streams = r.json().get("streams", [])
            for s in streams: s["_source"] = addon_url
            return streams
    except: pass
    return []

def _normalize_stremio_stream(s: dict) -> dict:
    title = s.get("title", "") or ""
    return {
        "title": title, "infoHash": s.get("infoHash"),
        "magnet": f"magnet:?xt=urn:btih:{s['infoHash']}" if s.get("infoHash") else None,
        "source": s.get("_source", ""), "fileIdx": s.get("fileIdx"),
        "quality": _extract_quality(title), "size": _extract_size(title), "seeders": 0, "dub_type": _nyaa_detect_type(title),
    }

def resolve_imdb_id(anime_name: str) -> Optional[str]:
    try:
        url = f"https://v3-cinemeta.strem.io/catalog/series/top/search={http_requests.utils.quote(anime_name)}.json"
        r = http_requests.get(url, timeout=8)
        if r.status_code == 200:
            metas = r.json().get("metas", [])
            if metas: return metas[0].get("id")
    except: pass
    return None

def resolve_kitsu_id(anime_name: str) -> Optional[str]:
    try:
        r = http_requests.get("https://kitsu.io/api/edge/anime", params={"filter[text]": anime_name, "page[limit]": 1}, headers={"Accept": "application/vnd.api+json"}, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data: return data[0]["id"]
    except: pass
    return None

def build_stremio_ids(imdb_id: Optional[str], kitsu_id: Optional[str], season: int, episode: int) -> List[str]:
    ids = []
    if imdb_id: ids.append(f"{imdb_id}:{season}:{episode}")
    if kitsu_id: ids.append(f"kitsu:{kitsu_id}:{season}:{episode}")
    return ids

def search_all_sources(name: str, season: int, episode: int, imdb_id: Optional[str], kitsu_id: Optional[str], use_nyaa: bool = True, nyaa_trusted: bool = False) -> List[dict]:
    all_streams: List[dict] = []
    futures_map = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        if imdb_id or kitsu_id:
            for addon in STREMIO_ADDONS:
                for mid in build_stremio_ids(imdb_id, kitsu_id, season, episode):
                    fut = ex.submit(_fetch_addon_streams, addon, "series", mid)
                    futures_map[fut] = ("stremio", addon)
        if use_nyaa and name:
            fut = ex.submit(search_nyaa, name, episode, season, nyaa_trusted)
            futures_map[fut] = ("nyaa", "nyaa.si")
        for future in as_completed(futures_map):
            try:
                batch = future.result()
                if futures_map[future][0] == "stremio": all_streams.extend([_normalize_stremio_stream(s) for s in batch])
                else: all_streams.extend(batch)
            except: pass
    return _sort_streams(_deduplicate(all_streams))

# ── TRANSCODE ENGINE ─────────────────────────────────────────────────────────
class FFmpegError(RuntimeError):
    def __init__(self, code, mode, video_codec, audio_codec, encoder, detail):
        self.code, self.mode, self.video_codec, self.audio_codec, self.encoder, self.detail = code, mode, video_codec, audio_codec, encoder, detail
    def to_dict(self):
        return {"error": "FFmpeg falhou", "ffmpeg_code": self.code, "detail": self.detail}

def detect_gpu_encoder() -> str:
    global _gpu_encoder
    with _gpu_lock:
        if _gpu_encoder is not None: return _gpu_encoder
        for encoder in [("h264_nvenc", "NVIDIA"), ("h264_qsv", "Intel")]:
            try:
                proc = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "nullsrc=s=64x64:d=1", "-c:v", encoder[0], "-frames:v", "1", "-f", "null", "-"], timeout=5, text=False)
                if proc.returncode == 0:
                    _gpu_encoder = encoder[0]
                    return _gpu_encoder
            except: pass
        _gpu_encoder = "libx264"
        return _gpu_encoder

def _hls_cache_dir(info_hash: str, mode: str) -> str:
    d = os.path.join(HLS_CACHE_PATH, f"{info_hash[:16]}_{mode}")
    os.makedirs(d, exist_ok=True)
    return d

def _probe_streams(file_path: str) -> dict:
    result = {"video_codec": "", "audio_codec": "", "audio_channels": 2, "video_pix_fmt": ""}
    try:
        proc = _run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path], timeout=15)
        streams = json.loads(proc.stdout).get("streams", [])
        for s in streams:
            if s.get("codec_type") == "video":
                result["video_codec"] = s.get("codec_name", "").lower()
                result["video_pix_fmt"] = s.get("pix_fmt", "").lower()
            elif s.get("codec_type") == "audio" and not result["audio_codec"]:
                result["audio_codec"] = s.get("codec_name", "").lower()
                result["audio_channels"] = s.get("channels", 2)
    except: pass
    return result

def auto_transcode_mode(file_path: str) -> str:
    info = _probe_streams(file_path)
    needs_v = info["video_codec"] in {"hevc", "h265", "av1", "vp9"} or "10le" in info["video_pix_fmt"]
    needs_a = info["audio_codec"] in AUDIO_TRANSCODE_CODECS
    return "full" if needs_v else ("audio" if needs_a else "copy")

def start_hls_transcode(info_hash: str, file_path: str, mode: str) -> str:
    hls_lock = _get_hls_lock(info_hash)
    cache_dir = _hls_cache_dir(info_hash, mode)
    m3u8_path = os.path.join(cache_dir, "index.m3u8")
    if os.path.exists(m3u8_path): return m3u8_path
    with hls_lock:
        if os.path.exists(m3u8_path): return m3u8_path
        wait_for_buffer(file_path)
        info = _probe_streams(file_path)
        encoder = detect_gpu_encoder()
        v_args = ["-c:v", "copy"] if mode in ("copy", "audio") else (["-c:v", encoder, "-preset", "fast", "-pix_fmt", "yuv420p"] if encoder != "libx264" else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"])
        a_args = ["-c:a", "copy"] if mode == "copy" else ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", file_path, "-map", "0:v:0", "-map", "0:a:0", *v_args, *a_args, "-f", "hls", "-hls_time", "6", "-hls_list_size", "0", "-hls_segment_filename", os.path.join(cache_dir, "seg%05d.ts"), m3u8_path]
        proc = _popen(cmd)
        with active_streams_lock:
            if info_hash in active_streams: active_streams[info_hash].setdefault("ffmpeg_procs", []).append(proc)
        for _ in range(60):
            if os.path.exists(os.path.join(cache_dir, "seg00000.ts")): return m3u8_path
            time.sleep(0.5)
        raise FFmpegError(-1, mode, info["video_codec"], info["audio_codec"], encoder, "Timeout HLS")

# ── FLASK ROUTES ──────────────────────────────────────────────────────────────
@app.route("/ping")
def ping(): return jsonify({"status": "online"})

@app.route("/events/global")
def sse_global():
    _start_sse_ticker()
    q = queue.Queue(maxsize=50)
    with _sse_global_queues_lock: _sse_global_queues.add(q)
    def generate():
        while True: yield q.get()
    return Response(generate(), mimetype="text/event-stream")

@app.route("/cast/devices")
def cast_devices():
    return jsonify({"devices": cast_manager.discover_devices(force=request.args.get("force")=="true")})

@app.route("/cast/play", methods=["POST"])
def cast_play():
    d = request.json
    return jsonify({"success": cast_manager.play_on_device(d['ip'], d['url'])})

@app.route("/addons/search")
def addon_search():
    name, season, ep = request.args.get("name"), int(request.args.get("season", 1)), int(request.args.get("episode", 1))
    iid, kid = resolve_imdb_id(name), resolve_kitsu_id(name)
    streams = search_all_sources(name, season, ep, iid, kid)
    return jsonify({"streams": streams})

@app.route("/addons/start", methods=["POST"])
def addon_start():
    ih, mag = request.json.get("infoHash"), request.json.get("magnet")
    if not mag: mag = f"magnet:?xt=urn:btih:{ih}"
    h, ih, fp, fs, ct = bootstrap_magnet(mag)
    wait_for_buffer(fp)
    mode = auto_transcode_mode(fp)
    threading.Thread(target=start_hls_transcode, args=(ih, fp, mode), daemon=True).start()
    return jsonify({"info_hash": ih, "stream_url": f"/stream/{ih}", "hls_url": f"/hls/{ih}/index.m3u8", "mode": mode, "tracks": get_track_info(fp)})

@app.route("/stream/<info_hash>")
def stream_by_hash(info_hash):
    with active_streams_lock: e = active_streams.get(info_hash.lower())
    if not e: return "Not found", 404
    return stream_file_response(e["file_path"], e["file_size"], e["content_type"])

@app.route("/hls/<info_hash>/index.m3u8")
def hls_playlist(info_hash):
    ih = info_hash.lower()
    with active_streams_lock: e = active_streams.get(ih)
    if not e: return "Not found", 404
    m = request.args.get("mode", "auto")
    if m == "auto": m = auto_transcode_mode(e["file_path"])
    p = start_hls_transcode(ih, e["file_path"], m)
    with open(p, "r") as f: content = f.read()
    return Response(re.sub(r"(seg\d+\.ts)", f"/hls/{ih}/\\1", content), mimetype="application/vnd.apple.mpegurl")

@app.route("/hls/<info_hash>/<segment>")
def hls_segment(info_hash, segment):
    ih = info_hash.lower()
    for mode in ("audio", "full", "copy"):
        p = os.path.join(HLS_CACHE_PATH, f"{ih[:16]}_{mode}", segment)
        if os.path.exists(p): return stream_file_response(p, os.path.getsize(p), "video/mp2t")
    return "Not ready", 503

@app.route("/stop", methods=["POST"])
def stop_torrent():
    ih = request.json.get("info_hash").lower()
    with active_streams_lock: e = active_streams.pop(ih, None)
    if e:
        for p in e.get("ffmpeg_procs", []): p.kill()
        cleanup_torrent(e["handle"], e["file_path"])
        return jsonify({"success": True})
    return "Not found", 404

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 1. Garante que FFmpeg existe
    if not ensure_ffmpeg():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Erro", "O FFmpeg não foi encontrado e não pôde ser baixado automaticamente.")
        sys.exit(1)

    # 2. Inicia interface de configuração
    result = show_config_window()
    if not result.get("start"):
        sys.exit(0)

    DOWNLOAD_PATH = result["path"]
    IS_TEMPORARY  = result["temporary"]
    HLS_CACHE_PATH = os.path.join(DOWNLOAD_PATH, ".hls_cache")
    os.makedirs(HLS_CACHE_PATH, exist_ok=True)

    print(f"📁 Downloads: {DOWNLOAD_PATH}")
    print(f"🚀 Servidor rodando em http://localhost:5000")

    stop_event = threading.Event()
    threading.Thread(target=run_tray, args=(DOWNLOAD_PATH, IS_TEMPORARY, stop_event), daemon=True).start()
    
    # Discovery de dispositivos DLNA em background
    threading.Thread(target=cast_manager.discover_devices, daemon=True).start()

    # Roda o Flask
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000, threaded=True), daemon=True).start()

    stop_event.wait()
    cleanup_all()
    sys.exit(0)