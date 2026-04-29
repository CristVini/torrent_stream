# TorrentStream 🎬

Servidor de streaming via torrent com suporte a HLS, transcodificação, legendas e controle de Smart TVs (DLNA/UPnP).

## Setup Rápido

### 1. Instalar dependências (primeira vez)

```bash
# Linux/Mac
sudo apt-get install -y python3-tk ffmpeg python3-venv  # Linux
brew install python3 ffmpeg                               # macOS

# Setup FFmpeg do projeto
python3 setup_ffmpeg.py
```

**Windows**: FFmpeg é baixado automaticamente durante `python3 setup_ffmpeg.py`

### 2. Instalar pacotes Python

```bash
# As dependências Python já estão em vendor/ 
# (instaladas durante desenvolvimento)
python3 torrent_stream.py
```

## Addons Customizados do Stremio

O TorrentStream suporta addons customizados do Stremio! Você pode especificar suas próprias URLs de addons via parâmetro `addons` na busca ou gerenciá-los na **interface gráfica**.

### Addons Padrão Incluídos:
- **Torrentio** - `https://torrentio.strem.fun`
- **MediaFusion** - `https://mediafusion.elfhosted.com`
- **Comet** - `https://comet.elfhosted.com`

### Gerenciamento via Interface Gráfica:

Na janela de configuração, clique na aba **"🔗 Addons"** para:
- ✅ **Adicionar** novos addons digitando a URL
- ✅ **Remover** addons selecionados na lista
- ✅ **Restaurar** addons padrão (remove todos os customizados)
- ✅ **Salvar automaticamente** as configurações

### Como Usar Addons Customizados:

```bash
# Via parâmetro (sobrescreve interface gráfica)
GET /addons/search?name=Breaking%20Bad&season=1&episode=1&addons=https://meu-addon.com

# Via interface gráfica (recomendado)
# 1. Execute torrent_stream.py
# 2. Na janela de configuração, vá para aba "🔗 Addons"
# 3. Adicione/remova URLs conforme necessário
# 4. Clique "▶ Iniciar Servidor"

# Múltiplos addons customizados
GET /addons/search?name=Breaking%20Bad&season=1&episode=1&addons=https://addon1.com,https://addon2.net,https://addon3.org
```

**Nota**: Os addons configurados na interface gráfica são salvos automaticamente e usados por padrão em todas as buscas.

## Configuração de Porta

Por padrão, o servidor roda na porta 5000. Para usar uma porta diferente:

```bash
# Porta 8080
PORT=8080 python3 torrent_stream.py

# Porta 3000
PORT=3000 python3 torrent_stream.py

# Porta 9000
PORT=9000 python3 torrent_stream.py
```

**Nota**: Todas as URLs retornadas pela API usarão a porta configurada automaticamente.

## Visão Geral do Projeto

```
torrent_stream/
├── torrent_stream.py       # Aplicação principal (Flask + libtorrent)
├── setup_ffmpeg.py         # Script de setup (baixa/vincula FFmpeg)
├── vendor/                 # Dependências Python empacotadas
│   ├── flask/
│   ├── flask_cors/
│   ├── pystray/
│   └── ...
├── ffmpeg_bin/             # Symlinks para FFmpeg (criado por setup_ffmpeg.py)
└── .gitignore
```

## API Endpoints

### Status & Health
- `GET /` - Documentação da API
- `GET /ping` - Status do servidor
- `GET /status` - Status geral e torrents ativos
- `GET /ffmpeg/status` - Status do FFmpeg
- `GET /transcode/test` - Teste de transcodificação

### Torrents
- `POST /addons/start` - Iniciar torrent (magnet/infoHash)
- `GET /stream/<info_hash>` - Stream direto do arquivo
- `POST /stop` - Parar torrent

### HLS / Transcodificação
- `GET /hls/<info_hash>/index.m3u8` - Playlist HLS
- `GET /hls/<info_hash>/<segment>` - Segmento HLS
- `POST /hls/select-audio/<info_hash>` - Selecionar áudio
- `GET /transcode/status/<info_hash>` - Status da transcodificação

### Busca
- `GET /search` - Busca combinada
- `GET /addons/search` - Busca em Stremio addons (suporta addons customizados)
- `GET /nyaa/search` - Busca Nyaa (anime)
- `GET /tracks/<info_hash>` - Info de áudio/legendas

### Legendas
- `GET /subtitles/<info_hash>/<index>.vtt` - Extrair legenda (VTT)
- `GET /subtitles/proxy` - Proxy para legendas remotas
- `GET /translate-sub/<info_hash>/<index>` - Traduzir legenda

### Smart TV (DLNA/UPnP)
- `GET /cast/devices` - Listar Smart TVs
- `POST /cast/play` - Enviar para TV
- `POST /cast/stop` - Parar na TV
- `POST /cast/pause` - Pausar na TV
- `POST /cast/volume` - Ajustar volume

### Server-Sent Events (SSE)
- `GET /events/global` - Eventos globais
- `GET /events/<info_hash>` - Eventos de um torrent

## Exemplo de Uso

```bash
# Iniciar servidor na porta padrão (5000)
python3 torrent_stream.py

# Iniciar servidor em porta customizada
PORT=8080 python3 torrent_stream.py

# Em outro terminal, fazer uma requisição de teste
curl http://localhost:5000/

# Ou na porta customizada
curl http://localhost:8080/

# Iniciar um torrent
curl -X POST http://localhost:5000/addons/start \
  -H "Content-Type: application/json" \
  -d '{
    "magnet": "magnet:?xt=urn:btih:08ada5c7c6ac0ec0e46cbbf5eb9245eb6d21feb9&dn=Sintel"
  }'

# Obter status
curl http://localhost:5000/status | jq '.torrents[0]'

# Ou na porta customizada:
curl -X POST http://localhost:8080/addons/start \
  -H "Content-Type: application/json" \
  -d '{
    "magnet": "magnet:?xt=urn:btih:08ada5c7c6ac0ec0e46cbbf5eb9245eb6d21feb9&dn=Sintel"
  }'
curl http://localhost:8080/status | jq '.torrents[0]'

# Buscar streams usando addons padrão
curl "http://localhost:5000/addons/search?name=Breaking%20Bad&season=1&episode=1"

# Buscar usando addons customizados
curl "http://localhost:5000/addons/search?name=Breaking%20Bad&season=1&episode=1&addons=https://meu-addon.com,https://outro-addon.net"

# Acessar HLS
curl http://localhost:5000/hls/08ada5c7c6ac0ec0e46cbbf5eb9245eb6d21feb9/index.m3u8

# Ou na porta customizada:
curl http://localhost:8080/hls/08ada5c7c6ac0ec0e46cbbf5eb9245eb6d21feb9/index.m3u8
```

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│               TorrentStream (Flask)                      │
├─────────────────────────────────────────────────────────┤
│  Libtorrent Session (BitTorrent)                        │
│  ├─ Download de metadata                               │
│  ├─ Gerencimento de peers                              │
│  └─ Buffer progressivo                                 │
├─────────────────────────────────────────────────────────┤
│  FFmpeg (HLS Transcoding)                               │
│  ├─ Vídeo: H.264 (CPU/GPU)                             │
│  ├─ Áudio: AAC (transcode se necessário)               │
│  └─ Legendas: VTT (extração + tradução)                │
├─────────────────────────────────────────────────────────┤
│  DLNA/UPnP Control (Smart TV Casting)                   │
├─────────────────────────────────────────────────────────┤
│  APIs Externas                                          │
│  ├─ Stremio Addons (busca de streams)                  │
│  ├─ Nyaa (anime torrents)                              │
│  ├─ Google Translate (legendas)                        │
│  └─ IMDB/Kitsu (metadados)                             │
└─────────────────────────────────────────────────────────┘
```

## Requisitos

### Sistema
- Python 3.8+
- Linux/macOS/Windows
- FFmpeg com ffprobe

### Python
- Flask / Flask-CORS
- libtorrent-rasterbar
- requests
- pystray (opcional, para system tray)

## Desenvolvimento

Para trabalhar com o projeto:

```bash
# Limpar cache Python
find . -type d -name __pycache__ -exec rm -rf {} \;

# Testar sintaxe
python3 -m py_compile torrent_stream.py

# Executar com debug
PYTHONUNBUFFERED=1 python3 torrent_stream.py
```

## Licença

Ver arquivo LICENSE
