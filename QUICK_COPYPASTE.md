# 💾 Quick Copy-Paste Code

## Use Este Documento Para Copiar/Colar Direto no torrent_stream.py

Cada seção está pronta para copiar integralmente. Basta seguir as instruções de onde inserir.

---

## [IMPORTS] Linhas ~10-40

Adicione APÓS os outros imports:

```python
import time
import statistics
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# Opcional: se já houver, não copie novamente
```

Se `TTLCache` não estiver disponível:
```python
try:
    from cachetools import TTLCache
    _has_cachetools = True
except ImportError:
    _has_cachetools = False
    TTLCache = dict  # fallback
    print("⚠️  cachetools not available, using simple dict for cache")
```

---

## [GLOBALS/CONSTANTS] Linhas ~407-450

Adicione APÓS `ADDON_HEADERS` e `ADDONS_FILE`:

```python
# ══════════════════════════════════════════════════════════════════════════════
# ── ADDON HEALTH CHECK & MANIFEST CACHE ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
ADDON_MANIFEST_CACHE = {}      # {url: {"manifest": {...}, "cached_at": timestamp}}
ADDON_HEALTH_CACHE = {}        # {url: {"online": bool, "checked_at": timestamp}}
MANIFEST_CACHE_TTL = 3600      # 1 hora
HEALTH_CHECK_TTL = 600         # 10 minutos

# ══════════════════════════════════════════════════════════════════════════════
# ── STREAM CACHE COM TTL ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
try:
    STREAMS_CACHE = TTLCache(maxsize=10000, ttl=14400)  # 4 horas
except:
    STREAMS_CACHE = {}  # Fallback simples

# ══════════════════════════════════════════════════════════════════════════════
# ── PERFORMANCE TRACKING ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
ADDON_STATS = {}  # {url: {"times": [...ms], "successes": int, "failures": int}}
MAX_ADDON_HISTORY = 100
```

---

## [FUNÇÕES NOVAS] Linhas 1646-1750 (antes de `resolve_imdb_id`)

### Bloco 1: Manifest & Health Check

```python
# ══════════════════════════════════════════════════════════════════════════════
# ── ADDON MANIFEST CACHE & HEALTH (NEW) ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _get_addon_base_url(addon_url: str) -> str:
    """Remove /manifest.json se presente, normaliza URL"""
    return addon_url.rstrip("/").replace("/manifest.json", "")

def _fetch_addon_manifest(addon_url: str, timeout: int = 8) -> Optional[dict]:
    """Obtém manifest do addon"""
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
    """Verifica se manifest está em cache"""
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
        return True
    
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

### Bloco 2: Performance Tracking

```python
# ══════════════════════════════════════════════════════════════════════════════
# ── ADDON PERFORMANCE TRACKING (NEW) ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

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
    
    total = stats["successes"] + stats["failures"]
    success_rate = min(stats["successes"] / total, 1.0)
    success_score = success_rate * 50
    
    avg_time = statistics.mean(stats["times"])
    if avg_time < 100:
        time_score = 40
    elif avg_time < 300:
        time_score = 20 + (40 - 20) * ((300 - avg_time) / 200)
    else:
        time_score = max(0, 20 - (avg_time - 300) / 50)
    
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
    
    for url in sorted_urls:
        score = scores[url]
        print(f"  {score:5.1f} - {url}")
    
    return sorted_urls
```

### Bloco 3: Cache TTL Support

```python
# ══════════════════════════════════════════════════════════════════════════════
# ── STREAM CACHE COM TTL (NEW) ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _parse_cache_ttl(cache_control_header: str) -> int:
    """Parse Cache-Control header para TTL"""
    if not cache_control_header:
        return 3600
    
    match = re.search(r'max-age=(\d+)', cache_control_header)
    if match:
        ttl = int(match.group(1))
        return min(ttl, 86400)
    
    return 3600

def _get_stream_cache_key(addon_url: str, media_type: str, media_id: str) -> str:
    """Chave única para cache"""
    return f"{addon_url}|{media_type}|{media_id}"

def clear_addon_cache(addon_url: Optional[str] = None):
    """Limpa cache global ou de um addon"""
    if addon_url is None:
        STREAMS_CACHE.clear()
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

## [SUBSTITUIR FUNÇÃO] _fetch_addon_streams()

**ENCONTRE e SUBSTITUA a função atual por:**

```python
def _fetch_addon_streams(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    """Fetch streams com cache TTL support"""
    cache_key = _get_stream_cache_key(addon_url, media_type, media_id)
    
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
                
                cache_control = r.headers.get('Cache-Control', '')
                ttl = _parse_cache_ttl(cache_control)
                STREAMS_CACHE[cache_key] = streams
                print(f"✅ Cached: {addon_url}")
                
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

## [SUBSTITUIR FUNÇÃO] search_all_sources()

**ENCONTRE e SUBSTITUA a função atual por:**

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
    """Busca com Priority-1 optimizations"""
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
            start_time = time.time()
            fut = ex.submit(search_nyaa, name, episode, season, nyaa_trusted)
            futures_map[fut] = ("nyaa", "nyaa.si", start_time)
        
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
                    stats = f" [{response_time_ms:.0f}ms]"
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

## ✅ Checklist Final

Após copiar/colar:

- [ ] Todos os imports adicionados
- [ ] Todos os globais/constants adicionados
- [ ] Bloco 1 (Manifest & Health) inserido
- [ ] Bloco 2 (Performance) inserido
- [ ] Bloco 3 (Cache TTL) inserido
- [ ] `_fetch_addon_streams()` substituída
- [ ] `search_all_sources()` substituída
- [ ] No errors ao salvar
- [ ] Testar: `python3 torrent_stream.py` inicia sem erro
- [ ] Testar: Fazer uma busca `/addons/search?name=Breaking%20Bad&season=1&episode=1`
- [ ] Verificar logs para "✅ Online" e "💾 Cache hit"

---

## 🧪 Teste Rápido

Após implementar:

```bash
# Terminal 1: Iniciar servidor
python3 torrent_stream.py

# Terminal 2: Fazer busca
curl "http://localhost:5000/addons/search?name=Breaking%20Bad&season=1&episode=1"

# Terminal 3: Monitor logs (deve ver em Terminal 1)
# - "✅ Online: https://torrentio.strem.fun"
# - "✅ Online: https://mediafusion.elfhosted.com"
# - "📊 Sorting by performance"
# - "✅ {addon}: X results [Yms]"
```

---

## ❓ Se Algo der Erro

### Erro: `ImportError: No module named 'cachetools'`
**Solução:** Já tem fallback no código, vai funcionar com dict simples

### Erro: `NameError: name 'statistics' is not defined`
**Solução:** Adicionar `import statistics` ao início

### Erro: `NameError: name 'ThreadPoolExecutor' is not defined`
**Solução:** Já deve estar importado, verificar imports

### Addons sempre offline?
**Debug:** 
```bash
curl https://torrentio.strem.fun/manifest.json
# Deve retornar JSON com metadata
```

### Cache não funciona?
**Debug:**
```python
print(STREAMS_CACHE)  # Ver estado do cache
clear_addon_cache()   # Limpar cache
print(STREAMS_CACHE)  # Deve estar vazio
```

---

## 🎯 Próximo Passo

Depois que Priority-1 funcionar:

1. Monitorar logs por 24h
2. Verificar impacto na performance
3. Avaliar se vale implementar Priority-2 (movie support, subtitles)
4. Considerar dashboard de stats (Priority-3)

---

**Pronto para copiar/colar!** 🚀
