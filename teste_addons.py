import requests
import json

# Configurações do Teste
STREMIO_ADDONS = [
    "https://torrentio.strem.fun",
    "https://comet.elfhosted.com",
    "https://mediafusion.elfhosted.com"
]

def realizar_busca(nome_exibicao, imdb_id, season, episode):
    print(f"\n{'='*60}")
    print(f"🔍 BUSCANDO: {nome_exibicao} (S{season:02d}E{episode:02d})")
    print(f"🆔 IMDB ID: {imdb_id}")
    print(f"{'='*60}\n")

    # Headers para simular um navegador e evitar bloqueios (403/Cloudflare)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://web.stremio.com/"
    }

    encontrou_total = 0

    for addon_url in STREMIO_ADDONS:
        # Formata a URL no padrão do Stremio: /stream/{type}/{id}:{season}:{episode}.json
        base_url = addon_url.replace("/manifest.json", "").rstrip("/")
        target_url = f"{base_url}/stream/series/{imdb_id}:{season}:{episode}.json"
        
        print(f"📡 Solicitando: {target_url}")

        try:
            response = requests.get(target_url, headers=headers, timeout=12)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    streams = data.get("streams", [])
                    
                    if streams:
                        print(f"✅ SUCESSO: {len(streams)} links encontrados no {addon_url}")
                        encontrou_total += len(streams)
                        # Mostra os 3 primeiros resultados
                        for i, s in enumerate(streams[:3]):
                            print(f"   [{i+1}] {s.get('title').splitlines()[0]}") # Pega só a primeira linha do título
                    else:
                        print(f"❌ VAZIO: O addon {addon_url} não retornou links para este ID.")
                
                except json.JSONDecodeError:
                    print(f"🔥 ERRO: O addon retornou HTML em vez de JSON (Provável bloqueio de IP).")
            
            elif response.status_code == 403:
                print(f"🚫 BLOQUEIO 403: O IP do Codespace foi rejeitado pelo addon {addon_url}.")
            else:
                print(f"⚠️ STATUS {response.status_code}: Problema na resposta do addon {addon_url}.")

        except requests.exceptions.Timeout:
            print(f"⏰ TIMEOUT: O addon {addon_url} demorou demais para responder.")
        except Exception as e:
            print(f"❗ FALHA: {e}")
        
        print("-" * 30)

    print(f"\n✨ FIM DO TESTE: Total de {encontrou_total} streams únicos encontrados.")
    if encontrou_total == 0:
        print("💡 DICA: Se tudo falhou no Codespace, tente rodar este mesmo script no seu PC local.")

if __name__ == "__main__":
    # Teste 1: Re:Zero Season 1 (Garantia de que o ID tt5607616 funciona)
    realizar_busca("Re:Zero", "tt5607616", season=1, episode=1)
    
    # Teste 2: Re:Zero Season 4 (O que você quer validar)
    # Nota: Se o Torrentio retornar vazio aqui, é porque ele ainda não indexou a S04 no ID tt5607616
    # realizar_busca("Re:Zero", "tt5607616", season=4, episode=1)