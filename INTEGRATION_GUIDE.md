# 🔧 Guia de Integração - Priority-1 no torrent_stream.py

## Visão Geral do Processo

Este guia mostra **exatamente onde** inserir o código Priority-1 no arquivo existente.

```
torrent_stream.py (estrutura atual):
├─ IMPORTS + CONFIG (linhas 1-500)
├─ FFmpeg Setup (linhas 100-200)
├─ CONFIG + CONSTANTS (linhas 407-450)
│  ├─ STREMIO_ADDONS
│  ├─ ADDON_HEADERS
│  └─ ADDON MANAGEMENT (load/save addons)
│
├─ STREMIO ADDON ENGINE (linhas 1646-1810)
│  ├─ _fetch_addon_streams()
│  ├─ _normalize_stremio_stream()
│  ├─ resolve_imdb_id()
│  ├─ resolve_kitsu_id()
│  ├─ build_stremio_ids()
│  └─ search_all_sources()
│
├─ FFmpeg Transcode Engine (linhas 1811+)
└─ Flask Routes + UI ...
```

---

## 📍 Inserções Necessárias

### 1. Adicionar Imports (Linhas 1-50)

**Adicionar após outros imports:**

```python
# ── Linhas ~30-40, adicionar: ────────────────────────────────────────────
import time
import statistics
from cachetools import TTLCache  # Se não estiver em vendor/

# OU se cachetools não estiver instalado:
# from typing import Dict, Optional
# import threading
```

---

### 2. Adicionar Constants/Globals (Linhas 407-450)

**Logo após `ADDON_HEADERS`, adicionar:**

```python
# ── ADDON MANIFEST CACHE & HEALTH (NEW) ─────────────────────────────────
ADDON_MANIFEST_CACHE = {}      # {url: {"manifest": {...}, "cached_at": t}}
ADDON_HEALTH_CACHE = {}        # {url: {"online": bool, "checked_at": t}}
MANIFEST_CACHE_TTL = 3600      # 1 hora
HEALTH_CHECK_TTL = 600         # 10 minutos

# ── STREAM CACHE COM TTL (NEW) ──────────────────────────────────────────
try:
    STREAMS_CACHE = TTLCache(maxsize=10000, ttl=14400)  # 4 horas
except ImportError:
    # Fallback: dicionário simples (sem TTL automático)
    STREAMS_CACHE = {}

# ── ADDON PERFORMANCE TRACKING (NEW) ────────────────────────────────────
ADDON_STATS = {}  # {url: {"times": [...], "successes": int, "failures": int}}
MAX_ADDON_HISTORY = 100
```

---

### 3. Seção "STREMIO ADDON ENGINE" (Linhas 1646+)

**Estrutura:**

```python
# ── STREMIO ADDON ENGINE ─────────────────────────────────────────────────
# [Código existente]
def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    # [Versão atual]
    ...

def _normalize_stremio_stream(s: dict) -> dict:
    # [Versão atual]
    ...

# ══════════════════════════════════════════════════════════════════════════
# === NOVAS FUNÇÕES: PRIORITY-1 ==========================================
# ══════════════════════════════════════════════════════════════════════════

# [INSERIR AQUI as 4 seções de novo código]

# === FIM: PRIORITY-1 ===================================================

def resolve_imdb_id(anime_name: str) -> Optional[str]:
    # [Versão atual]
    ...
```

---

## 🔑 Novo Código - 4 Seções

### SEÇÃO 1: Addon Manifest & Health Functions

```python
# ── ADDON MANIFEST CACHE & HEALTH ──────────────────────────────────────────
def _get_addon_base_url(addon_url: str) -> str:
    """Remove /manifest.json se presente, normaliza URL"""
    return addon_url.rstrip("/").replace("/manifest.json", "")

def _fetch_addon_manifest(addon_url: str, timeout: int = 8) -> Optional[dict]:
    """Obtém manifest do addon com suporte a cache"""
    base_url = _get_addon_base_url(addon_url)
    url = f"{base_url}/manifest.json"
    
    try:
        r = http_requests.get(url, headers=ADDON_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"❌ Manifest fetch error {addon_url}: {e}")
    
    return None

def _is_manifest_cached_valid(addon_url: str) -> bool:
    """Verifica se manifest está em cache e é válido"""
    if addon_url not in ADDON_MANIFEST_CACHE:
        return False
    
    entry = ADDON_MANIFEST_CACHE[addon_url]
    age = time.time() - entry["cached_at"]
    return age < MANIFEST_CACHE_TTL

def get_addon_manifest(addon_url: str) -> Optional[dict]:
    """Obtém manifest com cache automático"""
    if _is_manifest_cached_valid(addon_url):
        return ADDON_MANIFEST_CACHE[addon_url]["manifest"]
    
    manifest = _fetch_addon_manifest(addon_url)
    if manifest:
        ADDON_MANIFEST_CACHE[addon_url] = {
            "manifest": manifest,
            "cached_at": time.time()
        }
        print(f"✅ Manifest cached: {addon_url}")
    
    return manifest

def _addon_supports_media_type(addon_url: str, media_type: str = "series") -> bool:
    """Verifica se addon suporta tipo de mídia"""
    manifest = get_addon_manifest(addon_url)
    if not manifest:
        return True  # Fallback: assume suporte
    
    supported = manifest.get("supportedTypes", [])
    return media_type in supported or len(supported) == 0

def _check_addon_health(addon_url: str, timeout: int = 5) -> bool:
    """Verifica se addon está online"""
    try:
        base_url = _get_addon_base_url(addon_url)
        r = http_requests.get(f"{base_url}/manifest.json", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False

def _is_health_cached_valid(addon_url: str) -> bool:
    """Verifica se health check está em cache"""
    if addon_url not in ADDON_HEALTH_CACHE:
        return False
    
    entry = ADDON_HEALTH_CACHE[addon_url]
    age = time.time() - entry["checked_at"]
    return age < HEALTH_CHECK_TTL

def check_addon_health(addon_url: str, use_cache: bool = True) -> bool:
    """Verifica saúde do addon (online/offline)"""
    if use_cache and _is_health_cached_valid(addon_url):
        return ADDON_HEALTH_CACHE[addon_url]["online"]
    
    online = _check_addon_health(addon_url)
    ADDON_HEALTH_CACHE[addon_url] = {
        "online": online,
        "checked_at": time.time()
    }
    
    status = "✅ Online" if online else "❌ Offline"
    print(f"{status}: {addon_url}")
    
    return online

def get_healthy_addons(addon_urls: List[str], timeout_per_check: int = 3) -> List[str]:
    """Filtra apenas addons online"""
    if not addon_urls:
        return []
    
    if len(addon_urls) <= 2:
        return [url for url in addon_urls if check_addon_health(url)]
    
    # Paralelizar para múltiplos addons
    healthy = []
    with ThreadPoolExecutor(max_workers=min(5, len(addon_urls))) as ex:
        futures = {
            ex.submit(check_addon_health, url): url 
            for url in addon_urls
        }
        
        for future in as_completed(futures, timeout=timeout_per_check * len(addon_urls)):
            try:
                is_online = future.result(timeout=timeout_per_check)
                if is_online:
                    healthy.append(futures[future])
            except Exception:
                pass
    
    return healthy
```

---

### SEÇÃO 2: Performance Tracking Functions

```python
# ── ADDON PERFORMANCE TRACKING ──────────────────────────────────────────────
def _record_addon_request(addon_url: str, response_time_ms: float, success: bool):
    """Registra estatísticas de requisição"""
    if addon_url not in ADDON_STATS:
        ADDON_STATS[addon_url] = {
            "times": [],
            "successes": 0,
            "failures": 0,
            "last_success": 0,
        }
    
    stats = ADDON_STATS[addon_url]
    stats["times"].append(response_time_ms)
    
    if len(stats["times"]) > MAX_ADDON_HISTORY:
        stats["times"].pop(0)
    
    if success:
        stats["successes"] += 1
        stats["last_success"] = time.time()
    else:
        stats["failures"] += 1

def get_addon_score(addon_url: str) -> float:
    """Calcula performance score 0-100"""
    if addon_url not in ADDON_STATS:
        return 50.0
    
    stats = ADDON_STATS[addon_url]
    
    if not stats["times"] or (stats["successes"] + stats["failures"]) == 0:
        return 50.0
    
    # Success rate (0-50 pontos)
    total = stats["successes"] + stats["failures"]
    success_rate = min(stats["successes"] / total, 1.0)
    success_score = success_rate * 50
    
    # Response time (0-40 pontos)
    avg_time = statistics.mean(stats["times"])
    if avg_time < 100:
        time_score = 40
    elif avg_time < 300:
        time_score = 20 + (40 - 20) * ((300 - avg_time) / 200)
    else:
        time_score = max(0, 20 - (avg_time - 300) / 50)
    
    # Consistency (0-10 pontos)
    if len(stats["times"]) > 1:
        stdev = statistics.stdev(stats["times"])
        consistency_penalty = min(stdev / 200, 1.0)
        consistency_score = 10 * (1 - consistency_penalty)
    else:
        consistency_score = 10
    
    total_score = success_score + time_score + consistency_score
    return min(100, max(0, total_score))

def sort_addons_by_performance(addon_urls: List[str]) -> List[str]:
    """Ordena addons por performance score"""
    scores = {url: get_addon_score(url) for url in addon_urls}
    sorted_urls = sorted(addon_urls, key=lambda x: scores[x], reverse=True)
    
    # Log
    for url in sorted_urls:
        score = scores[url]
        print(f"  {score:5.1f} - {url}")
    
    return sorted_urls
```

---

### SEÇÃO 3: Cache with TTL Functions

```python
# ── STREAM CACHE COM TTL ────────────────────────────────────────────────────
def _parse_cache_ttl(cache_control_header: str) -> int:
    """Parse Cache-Control header para TTL"""
    if not cache_control_header:
        return 3600
    
    match = re.search(r'max-age=(\d+)', cache_control_header)
    if match:
        ttl = int(match.group(1))
        return min(ttl, 86400)  # Max 24h
    
    return 3600

def _get_stream_cache_key(addon_url: str, media_type: str, media_id: str) -> str:
    """Chave única para cache de streams"""
    return f"{addon_url}|{media_type}|{media_id}"

def clear_addon_cache(addon_url: Optional[str] = None):
    """Limpa cache (global ou de um addon)"""
    if addon_url is None:
        STREAMS_CACHE.clear() if isinstance(STREAMS_CACHE, dict) else None
        print("🗑 Cache limpo (todos addons)")
    else:
        if isinstance(STREAMS_CACHE, dict):
            keys_to_remove = [
                k for k in STREAMS_CACHE.keys() 
                if k.startswith(f"{addon_url}|")
            ]
            for k in keys_to_remove:
                del STREAMS_CACHE[k]
            print(f"🗑 Cache limpo ({addon_url})")
```

---

### SEÇÃO 4: Versão Melhorada de `_fetch_addon_streams()`

**SUBSTITUIR a função existente por:**

```python
def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    """Fetch streams com cache e TTL support"""
    cache_key = _get_stream_cache_key(addon_url, media_type, media_id)
    
    # ✨ Check cache
    if cache_key in STREAMS_CACHE:
        print(f"💾 Cache hit: {addon_url}")
        return STREAMS_CACHE[cache_key]
    
    base_url = addon_url.rstrip("/").replace("/manifest.json", "")
    target_url = f"{base_url}/stream/{media_type}/{media_id}.json"
    
    try:
        r = http_requests.get(target_url, headers=ADDON_HEADERS, timeout=12)
        if r.status_code == 200:
            try:
                streams = r.json().get("streams", [])
                for s in streams:
                    s["_source"] = addon_url
                
                # ✨ Parse TTL e cachear
                cache_control = r.headers.get('Cache-Control', '')
                ttl = _parse_cache_ttl(cache_control)
                STREAMS_CACHE[cache_key] = streams
                print(f"✅ Cached: {addon_url} ({ttl}s)")
                
                return streams
            except ValueError:
                print(f"❌ JSON inválido de {addon_url}")
        else:
            print(f"⚠ {addon_url} retornou {r.status_code}")
    except Exception as e:
        print(f"❗ Falha em {addon_url}: {e}")
    
    return []
```

---

### SEÇÃO 5: Versão Melhorada de `search_all_sources()`

**SUBSTITUIR a função existente por:**

```python
def search_all_sources(
    name: str,
    season: int,
    episode: int,
    imdb_id: Optional[str],
    kitsu_id: Optional[str],
    use_nyaa: bool = True,
    nyaa_trusted: bool = False,
    addon_urls: Optional[List[str]] = None,
) -> List[dict]:
    """
    Busca com Priority-1 optimizations:
    1. Health checks
    2. Performance tracking
    3. Cache support
    """
    if addon_urls is None:
        addon_urls = STREMIO_ADDONS.copy()
    
    print(f"\n🚀 Search: {name} S{season}E{episode}")
    
    # === 1. Health Checks ===
    print(f"🔍 Health check ({len(addon_urls)} addons)")
    addon_urls = get_healthy_addons(addon_urls)
    if not addon_urls:
        addon_urls = STREMIO_ADDONS.copy()
    print(f"✅ {len(addon_urls)} online")
    
    # === 2. Sort by Performance ===
    print(f"📊 Sorting by performance")
    addon_urls = sort_addons_by_performance(addon_urls)
    
    # === 3. Parallel Search ===
    print(f"🌐 Fetching streams")
    all_streams: List[dict] = []
    futures_map = {}
    
    with ThreadPoolExecutor(max_workers=16) as ex:
        
        if imdb_id or kitsu_id:
            ids_to_try = build_stremio_ids(imdb_id, kitsu_id, season, episode)
            
            for addon in addon_urls:
                if not _addon_supports_media_type(addon, "series"):
                    continue
                
                for mid in ids_to_try:
                    start_time = time.time()
                    fut = ex.submit(_fetch_addon_streams, addon, "series", mid)
                    futures_map[fut] = ("stremio", addon, start_time)
        
        if use_nyaa and name:
            fut = ex.submit(search_nyaa, name, episode, season, nyaa_trusted)
            futures_map[fut] = ("nyaa", "nyaa.si", time.time())
        
        # === Collect & Track ===
        for future in as_completed(futures_map):
            source_type, source_name, start_time = futures_map[future]
            response_time_ms = (time.time() - start_time) * 1000
            
            try:
                batch = future.result()
                success = len(batch) > 0
                
                if source_type == "stremio":
                    _record_addon_request(source_name, response_time_ms, success)
                    stats = f" [{response_time_ms:.0f}ms]"
                    print(f"✅ {source_name}: {len(batch)} results{stats}")
                    all_streams.extend([_normalize_stremio_stream(s) for s in batch])
                else:
                    print(f"✅ {source_name}: {len(batch)} results{stats}")
                    all_streams.extend(batch)
            
            except Exception as e:
                if source_type == "stremio":
                    _record_addon_request(source_name, response_time_ms, False)
                print(f"❌ {source_name}: {e}")
    
    # === Post-process ===
    print(f"📦 Post-processing")
    unique = _deduplicate(all_streams)
    sorted_streams = _sort_streams(unique)
    print(f"Total: {len(sorted_streams)} unique streams\n")
    
    return sorted_streams
```

---

## 📍 Checklist de Integração

- [ ] Adicionar imports (time, statistics, TTLCache)
- [ ] Adicionar constants (caches, TTLs)
- [ ] Adicionar SEÇÃO 1 (manifest + health)
- [ ] Adicionar SEÇÃO 2 (performance tracking)
- [ ] Adicionar SEÇÃO 3 (cache TTL)
- [ ] SUBSTITUIR `_fetch_addon_streams()` - SEÇÃO 4
- [ ] SUBSTITUIR `search_all_sources()` - SEÇÃO 5
- [ ] Testar busca simples
- [ ] Testar com addon offline
- [ ] Verificar logs

---

## 🧪 Testes Rápidos

### Teste 1: Health Check
```bash
# Deve marcar addons online/offline
curl http://localhost:5000/addons/search?name=Breaking%20Bad&season=1&episode=1
```

Logs esperados:
```
🔍 Health check (3 addons)
✅ Online: https://torrentio.strem.fun
✅ Online: https://mediafusion.elfhosted.com
✅ Online: https://comet.elfhosted.com
✅ 3 online
```

### Teste 2: Cache Hit
```bash
# Executar mesma busca 2x
curl http://localhost:5000/addons/search?name=Breaking%20Bad&season=1&episode=1
curl http://localhost:5000/addons/search?name=Breaking%20Bad&season=1&episode=1  # Com cache
```

Logs esperados (2ª vez):
```
💾 Cache hit: https://torrentio.strem.fun
💾 Cache hit: https://mediafusion.elfhosted.com
```

### Teste 3: Performance Tracking
```bash
# Ver stats (requer endpoint novo)
curl http://localhost:5000/addon-stats  # (opcional)
```

---

## 🐛 Troubleshooting

### ImportError: No module named 'cachetools'
**Solução 1:** Usar fallback (dicionário simples)
```python
try:
    from cachetools import TTLCache
    STREAMS_CACHE = TTLCache(maxsize=10000, ttl=14400)
except ImportError:
    STREAMS_CACHE = {}  # Fallback sem TTL automático
    print("⚠ Warning: cachetools not available, using simple dict cache")
```

**Solução 2:** Instalar cachetools
```bash
pip install cachetools
```

### Addons always offline
Possíveis causas:
- [ ] Falta de internet
- [ ] Firewall bloqueando requisições
- [ ] Addons realmente offline

Debug:
```bash
curl -I https://torrentio.strem.fun/manifest.json
```

### Cache muito agressivo (stale results)
Aumentar TTL:
```python
MANIFEST_CACHE_TTL = 7200  # 2 horas
HEALTH_CHECK_TTL = 1200    # 20 minutos
```

---

## 📊 Monitoramento

### Logs Importantes
Filtrar por:
- `✅ Online/Offline` - Health check results
- `💾 Cache hit` - Cache efficiency
- `📊 Sorting by performance` - Addon ordering
- `❌ JSON inválido` - Malformed responses

### Métricas para Acompanhar
```
REQUEST TYPES:
- Successful: len(batch) > 0
- Cached: "Cache hit" em log
- Failed: Exception em future.result()

TIMING:
- Avg response_time_ms por addon
- Total busca: start → finish

QUALITY:
- Total streams únicos
- % de duplicatas removidas
```

---

## ✅ Pronto para Integrar?

Se tudo acima faz sentido, siga a ordem:
1. Copiar SEÇÃO 1 (manifest + health)
2. Copiar SEÇÃO 2 (performance)
3. Copiar SEÇÃO 3 (cache)
4. Substituir função `_fetch_addon_streams()`
5. Substituir função `search_all_sources()`
6. Testar e debugar

**Tempo estimado:** 30-60 minutos de trabalho
