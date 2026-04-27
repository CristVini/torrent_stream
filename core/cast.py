import threading
import time
from typing import List, Dict
from core.dlnap import DlnapDevice, discover

class CastManager:
    def __init__(self):
        self.devices: List[DlnapDevice] = []
        self._lock = threading.Lock()
        self._last_discovery = 0

    def discover_devices(self, force: bool = False) -> List[Dict[str, str]]:
        """Descobre dispositivos DLNA na rede local."""
        now = time.time()
        # Cache de 30 segundos para não sobrecarregar a rede
        if not force and (now - self._last_discovery < 30) and self.devices:
            return self._get_device_list()

        print("🔍 Buscando dispositivos DLNA na rede...")
        try:
            # discover() retorna uma lista de dispositivos encontrados
            found = discover(timeout=5)
            with self._lock:
                self.devices = found
                self._last_discovery = now
        except Exception as e:
            print(f"⚠ Erro na descoberta DLNA: {e}")
        
        return self._get_device_list()

    def _get_device_list(self) -> List[Dict[str, str]]:
        return [
            {"name": d.name, "ip": d.ip, "location": d.location}
            for d in self.devices
        ]

    def play_on_device(self, device_ip: str, url: str) -> bool:
        """Envia um comando de reprodução para um dispositivo específico."""
        target = None
        with self._lock:
            for d in self.devices:
                if d.ip == device_ip:
                    target = d
                    break
        
        if not target:
            # Se não estiver no cache, tenta criar o dispositivo diretamente pelo IP
            try:
                target = DlnapDevice(ip=device_ip, timeout=5)
            except Exception:
                return False

        print(f"📺 Enviando vídeo para {target.name} ({device_ip})...")
        try:
            # dlnap lida com o protocolo UPnP AVTransport
            target.set_current_media(url)
            target.play()
            return True
        except Exception as e:
            print(f"❌ Erro ao enviar para TV: {e}")
            return False

    def stop_device(self, device_ip: str) -> bool:
        """Para a reprodução no dispositivo."""
        try:
            target = DlnapDevice(ip=device_ip, timeout=5)
            target.stop()
            return True
        except Exception:
            return False

# Instância global
cast_manager = CastManager()
