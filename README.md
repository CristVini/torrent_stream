# TorrentStream

Engine web para streaming via torrent com suporte a Stremio addons, HLS e SSE.

> Este é o único documento oficial. Todos os outros arquivos .md foram removidos.

## Por que usar

- `GET /addons/search` traz resultados de múltiplas fontes.
- `GET /events/<info_hash>` oferece progresso em tempo real via SSE.
- `GET /hls/<info_hash>/index.m3u8` permite reprodução de vídeo em players web.
- `GET /health` e `GET /addons/status` garantem controle de saúde e performance.

## Como iniciar

```bash
python torrent_stream.py
```

Servidor padrão: `http://localhost:5000`

Para porta customizada:

```bash
PORT=8080 python torrent_stream.py
```

## Endpoints principais

### Saúde e controle
- `GET /ping` — status do servidor
- `GET /health` — saúde do engine + addons
- `GET /addons/status` — ranking rápido dos addons
- `GET /addons/config` — configuração atual de addons
- `POST /addons/config` — definir addons customizados
- `DELETE /addons/config` — voltar aos addons padrão
- `GET /ffmpeg/status` — status do FFmpeg

### Busca de streams
- `GET /addons/search` — buscar streams em Stremio addons

Parâmetros úteis:
- `name`
- `imdb_id`
- `kitsu_id`
- `season`
- `episode`
- `nyaa`
- `nyaa_trusted`
- `addons`

Exemplo:

```bash
curl "http://localhost:5000/addons/search?name=Jujutsu+Kaisen&season=1&episode=1&nyaa=true"
```

### Addons customizados
- `GET /addons/config` retorna os addons configurados
- `POST /addons/config` salva uma lista nova de addons
- `DELETE /addons/config` restaura os addons padrão

Exemplo POST:

```bash
curl -X POST http://localhost:5000/addons/config   -H "Content-Type: application/json"   -d '{"addons": ["https://torrentio.strem.fun"]}'
```

### SSE (recomendado para UI)
- `GET /events/global` — eventos globais do servidor
- `GET /events/<info_hash>` — progresso do torrent específico

Exemplo SSE em JavaScript:

```js
const source = new EventSource('http://localhost:5000/events/abc123def456');
source.addEventListener('progress', event => {
  const data = JSON.parse(event.data);
  console.log('progress', data);
});
```

### Streaming de vídeo
- `GET /stream/<info_hash>` — stream direto do arquivo
- `GET /hls/<info_hash>/index.m3u8` — playlist HLS
- `GET /hls/<info_hash>/<segment>` — segmentos HLS

Exemplo HLS:

```html
<video controls>
  <source src="http://localhost:5000/hls/abc123def456/index.m3u8" type="application/x-mpegURL">
</video>
```

## Uso rápido

1. `GET /health`
2. `GET /addons/search?name=SeuAnime&season=1&episode=1`
3. `GET /events/<info_hash>` via SSE
4. `GET /hls/<info_hash>/index.m3u8`

## Apenas um documento

- Todos os outros arquivos markdown foram removidos.
- Este `README.md` é o único guia oficial do projeto.
