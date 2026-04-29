# 📊 Análise do Stremio Addon Engine

## Estado Atual do Engine

### Implementação Existente
O projeto atualmente utiliza uma integração **básica** com Stremio addons:

```
_fetch_addon_streams() 
  → GET /stream/{media_type}/{media_id}.json
  → Parse streams[]
  → _normalize_stremio_stream() → formato interno
  
search_all_sources()
  → ThreadPoolExecutor (max_workers=16)
  → build_stremio_ids() → gera variações de IDs
  → resolve_imdb_id() + resolve_kitsu_id() (paralelo)
  → Busca em múltiplos addons em paralelo
  → Deduplica + ordena resultados
```

**Funcionalidades Presentes:**
- ✅ Suporte a addons customizados (via parâmetro `addons`)
- ✅ Múltiplas tentativas de IDs (imdb, kitsu, season/episode variations)
- ✅ Busca paralela em múltiplos addons (ThreadPoolExecutor)
- ✅ Normalização de streams
- ✅ Fallback para Nyaa
- ✅ Deduplicação de resultados

---

## 🎯 Melhorias Propostas (Baseadas em Stremio Spec)

### 1. **Suporte Completo ao Manifest Addon**
**Problema:** Não valida se o addon está online ou suporta o tipo de conteúdo

**Solução:** Implementar cache de manifest com validação
```python
def _fetch_addon_manifest(addon_url: str) -> Optional[dict]:
    """Obtém e cacheia o manifest do addon para validação"""
    # GET {addon_url}/manifest.json
    # Verifica: supportedTypes, catalogs, durationMs (TTL)
```

**Benefícios:**
- Validar addon antes de fazer requisição de stream
- Respeitar TTL (Time-To-Live) do addon para cache
- Detectar addons descontinuados (manifest offline)
- Priorizar addons por performance (responseTime)

---

### 2. **Suporte a Movie vs Series**
**Problema:** Hardcoded para buscar só "series", ignora movies

**Solução:**
```python
def search_all_sources(..., media_type: str = "series"):
    # Se name for filme conhecido: media_type = "movie"
    # Se name for série: media_type = "series"
    # Buscar em ambos se uncertain
```

**Aplicação:**
- Detectar filme vs série via IMDB ID
- Ajustar `build_stremio_ids()` para remover season/episode em movies
- Formato movie: `imdb_id` (sem season:episode)

---

### 3. **Root IDs sem Season/Episode**
**Problema:** Build de IDs assume sempre series format

**Solução:**
```python
def build_stremio_ids(imdb_id, kitsu_id, season, episode, is_movie=False):
    ids = []
    if is_movie and imdb_id:
        return [imdb_id]  # Apenas o ID base
    
    # ... resto da lógica para series
```

---

### 4. **Respeitar Cache Headers & TTL**
**Problema:** Não respeita `Cache-Control` headers dos addons

**Solução:**
```python
def _fetch_addon_streams(...) -> List[dict]:
    r = http_requests.get(...)
    cache_control = r.headers.get('Cache-Control', '')
    # max-age=3600 → cachear por 1 hora
    # Implementar LRU cache com TTL
```

**Implementação:**
```python
from functools import lru_cache
from cachetools import TTLCache

_addon_cache = TTLCache(maxsize=1000, ttl=3600)

def _fetch_addon_streams_cached(addon_url, media_type, media_id):
    key = f"{addon_url}:{media_type}:{media_id}"
    if key in _addon_cache:
        return _addon_cache[key]
    
    streams = _fetch_addon_streams(addon_url, media_type, media_id)
    _addon_cache[key] = streams
    return streams
```

---

### 5. **Suporte a Subtitles da API Stremio**
**Problema:** Não obtém legendas dos addons

**Solução:**
```python
def _fetch_addon_subtitles(addon_url: str, media_type: str, media_id: str) -> List[dict]:
    """GET /subtitles/{media_type}/{media_id}.json"""
    base_url = addon_url.rstrip("/")
    url = f"{base_url}/subtitles/{media_type}/{media_id}.json"
    try:
        r = http_requests.get(url, timeout=8)
        return r.json().get("subtitles", []) if r.status_code == 200 else []
    except:
        return []
```

**Formato retornado:**
```json
{
  "subtitles": [
    {
      "lang": "en",
      "url": "https://..../sub.vtt",
      "id": "english-dub"
    }
  ]
}
```

---

### 6. **Priorização e Weighting de Addons**
**Problema:** Todos addons têm peso igual, ignore performance

**Solução:**
```python
# Rastrear por addon: tempo resposta, sucesso, hits
ADDON_STATS = {
    "addon_url": {
        "response_time_ms": [100, 120, 95],
        "success_rate": 0.95,
        "stream_count_avg": 15,
        "last_updated": 1234567890
    }
}

def _get_addon_priority(addon_url: str) -> float:
    """Retorna prioridade baseada em histório"""
    if addon_url not in ADDON_STATS:
        return 1.0
    
    stats = ADDON_STATS[addon_url]
    avg_time = sum(stats["response_time_ms"]) / len(stats["response_time_ms"])
    
    # Penalizar addons lentos
    time_penalty = min(avg_time / 300, 1.0)  # max 300ms
    success_bonus = stats["success_rate"]
    
    return (1.0 - time_penalty) * success_bonus
```

**Uso:**
```python
# Ordenar addons por prioridade antes de buscar
sorted_addons = sorted(addon_urls, 
                      key=lambda a: _get_addon_priority(a), 
                      reverse=True)
```

---

### 7. **Metadata Addon (Cinemeta)**
**Problema:** Usa Cinemeta apenas para resolver IDs

**Solução:** Expandir para metadata completo
```python
def _fetch_addon_metadata(addon_url: str, media_type: str, media_id: str) -> Optional[dict]:
    """GET /meta/{media_type}/{media_id}.json"""
    base_url = addon_url.rstrip("/")
    url = f"{base_url}/meta/{media_type}/{media_id}.json"
    try:
        r = http_requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json().get("meta")
    except:
        pass
    return None
```

**Informações disponíveis:**
- `name`, `type`, `imdbID`, `kitsuID`
- `poster`, `background`, `description`
- `runtime`, `released`, `videos[]` (trailers, clips)
- `genres[]`, `director[]`, `cast[]`

---

### 8. **Tratamento de Erros Robusto**
**Problema:** Addons offline degrade performance, sem retry inteligente

**Solução:**
```python
class AddonError(Enum):
    OFFLINE = "addon_offline"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"

def _fetch_addon_streams_robust(addon_url, media_type, media_id):
    """Retry com backoff exponencial"""
    for attempt in range(3):
        try:
            # ... fetch code
        except requests.Timeout:
            if attempt < 2:
                time.sleep(2 ** attempt)  # exponential backoff
            else:
                raise
        except requests.ConnectionError:
            # Marcar addon como offline temporariamente
            _mark_addon_offline(addon_url, duration_secs=300)
            return []
```

---

### 9. **Live Catalog Support**
**Problema:** Não implementa browsing de catálogos

**Solução:** Adicionar endpoints para browse
```python
def _fetch_addon_catalog(addon_url: str, catalog_id: str, 
                         skip: int = 0, limit: int = 50) -> List[dict]:
    """GET /catalog/{catalog_id}.json?skip={skip}&limit={limit}"""
    base_url = addon_url.rstrip("/")
    url = f"{base_url}/catalog/{catalog_id}.json"
    params = {"skip": skip, "limit": limit}
    
    r = http_requests.get(url, params=params, timeout=10)
    return r.json().get("metas", []) if r.status_code == 200 else []
```

**Exemplo de catálogos:**
- `movie/new` - Filmes novos
- `series/trending` - Séries em alta
- `series/anime` - Anime (Kitsu)

---

### 10. **Advanced Search & Filter**
**Problema:** Sem filtros por qualidade, seeds, tamanho

**Solução:** Expandir normalização
```python
def _normalize_stremio_stream(s: dict) -> dict:
    title = s.get("title", "") or ""
    
    # Extrair metadata do título
    quality = _extract_quality(title)  # ✅ Já existe
    size = _extract_size(title)        # ✅ Já existe
    seeders = _extract_seeders(title)  # ❌ Não existe
    leech_count = _extract_leechers(title)
    
    # Novos campos
    has_hardcoded_subs = bool(re.search(r'(hardsub|hard.?sub)', title, re.I))
    dub_type = _nyaa_detect_type(title)
    language = _detect_language(title)
    
    return {
        "title": title,
        "infoHash": s.get("infoHash"),
        "magnet": f"magnet:?xt=urn:btih:{s['infoHash']}" if s.get("infoHash") else None,
        "source": s.get("_source", ""),
        "fileIdx": s.get("fileIdx"),
        "quality": quality,
        "size": size,
        "seeders": seeders,
        "leechers": leech_count,
        "dub_type": dub_type,
        "language": language,
        "has_hardcoded_subs": has_hardcoded_subs,
    }
```

---

### 11. **Manifest Validation & Health Check**
**Problema:** Não detecta addons offline até fazer requisição

**Solução:**
```python
async def _health_check_addon(addon_url: str, timeout: int = 5) -> bool:
    """Verifica se addon está online antes de usar"""
    try:
        base_url = addon_url.rstrip("/")
        manifest_url = f"{base_url}/manifest.json"
        r = http_requests.get(manifest_url, timeout=timeout)
        return r.status_code == 200
    except:
        return False

def _get_available_addons(addon_urls: List[str]) -> List[str]:
    """Filtra só addons que estão online"""
    with ThreadPoolExecutor(max_workers=len(addon_urls)) as ex:
        futures = {
            ex.submit(_health_check_addon, url): url 
            for url in addon_urls
        }
        return [url for future in as_completed(futures) 
                if future.result()]
```

---

### 12. **Deduplication Strategy Melhoria**
**Problema:** Deduplicação atual pode ser simplista

**Sugestão:** Comparar múltiplos campos
```python
def _stream_fingerprint(s: dict) -> str:
    """Cria fingerprint único para stream"""
    hash_val = s.get("infoHash", "").upper()
    file_idx = s.get("fileIdx", -1)
    size = s.get("size", "unknown")
    return f"{hash_val}:{file_idx}:{size}"

def _deduplicate_advanced(streams: List[dict]) -> List[dict]:
    """Dedup por hash + file index + size"""
    seen = {}
    for s in streams:
        fp = _stream_fingerprint(s)
        if fp not in seen:
            seen[fp] = s
        else:
            # Manter versão com mais metadata
            if len(str(s)) > len(str(seen[fp])):
                seen[fp] = s
    return list(seen.values())
```

---

## 📈 Roadmap de Implementação

### Priority 1 (Alta - Quick wins)
- [ ] Implement addon manifest fetching + caching
- [ ] Addon health checks before search
- [ ] Response time tracking per addon
- [ ] TTL/Cache-Control support

### Priority 2 (Média)
- [ ] Movie vs Series detection
- [ ] Addon subtitles integration
- [ ] Better error handling + offline detection
- [ ] Advanced deduplication

### Priority 3 (Baixa - Future)
- [ ] Live catalog browsing
- [ ] Metadata enrichment (posters, descriptions)
- [ ] Advanced filtering UI
- [ ] Addon rating system

---

## 💡 Performance Estimations

| Feature | Est. Impact | Effort | ROI |
|---------|-------------|--------|-----|
| Manifest caching | +20% speed | Low | High |
| Health checks | -false-drops | Low | High |
| Performance tracking | +15% ranking | Medium | High |
| TTL caching | +40% speed (repeated) | Low | High |
| Movie support | +feature | Medium | Medium |
| Subtitles support | +feature | Medium | Medium |

---

## 🔗 Stremio Official Documentation
- [Stremio Addon Protocol](https://docs.stremio.com/docs/protocol)
- [Streams API](https://docs.stremio.com/docs/protocol#stream-resource)
- [Subtitles API](https://docs.stremio.com/docs/protocol#subtitle-resource)
- [Metadata API](https://docs.stremio.com/docs/protocol#meta-resource)
- [Catalogs API](https://docs.stremio.com/docs/protocol#catalog-resource)
