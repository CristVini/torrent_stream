#!/usr/bin/env python3
"""
Script de teste para validar os novos endpoints do Torrent Stream Engine v3.2.0
"""

import requests
import json
import time
import sys

BASE_URL = "http://localhost:5000"

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    END = '\033[0m'

def test_endpoint(name, method, endpoint, data=None):
    """Testa um endpoint"""
    print(f"\n{Colors.BLUE}→ {name}{Colors.END}")
    print(f"  {method} {endpoint}")
    
    try:
        url = f"{BASE_URL}{endpoint}"
        
        if method == "GET":
            response = requests.get(url, timeout=10)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=10)
        elif method == "DELETE":
            response = requests.delete(url, timeout=10)
        else:
            raise ValueError(f"Método desconhecido: {method}")
        
        if response.status_code == 200:
            json_data = response.json()
            print(f"  {Colors.GREEN}✅ Status: {response.status_code}{Colors.END}")
            return json_data
        else:
            print(f"  {Colors.RED}❌ Status: {response.status_code}{Colors.END}")
            print(f"  Erro: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        print(f"  {Colors.RED}❌ Não conseguiu conectar ao servidor{Colors.END}")
        print(f"  Verifique se o servidor está rodando: python torrent_stream.py")
        return None
    except Exception as e:
        print(f"  {Colors.RED}❌ Erro: {e}{Colors.END}")
        return None

def print_json(data, max_depth=2, current_depth=0):
    """Imprime JSON de forma legível"""
    if current_depth > max_depth:
        print("  " * (current_depth + 1) + "...")
        return
    
    if isinstance(data, dict):
        for key, value in list(data.items())[:3]:  # Mostrar apenas 3 primeiros
            if isinstance(value, (dict, list)):
                print(f"  " * (current_depth + 1) + f"• {Colors.YELLOW}{key}{Colors.END}")
                print_json(value, max_depth, current_depth + 1)
            else:
                print(f"  " * (current_depth + 1) + f"• {Colors.YELLOW}{key}{Colors.END}: {value}")
        if len(data) > 3:
            print(f"  " * (current_depth + 1) + f"... (+{len(data) - 3} mais)")
    elif isinstance(data, list):
        print(f"  " * (current_depth + 1) + f"[Lista com {len(data)} itens]")
        if len(data) > 0 and isinstance(data[0], dict):
            print_json(data[0], max_depth, current_depth + 1)

def main():
    print(f"\n{Colors.BLUE}=" * 60)
    print("🧪 Teste dos Novos Endpoints - Torrent Stream v3.2.0")
    print("=" * 60 + f"{Colors.END}\n")
    
    print("⏳ Aguardando conexão ao servidor...")
    
    # Tentar conectar
    for i in range(5):
        try:
            response = requests.get(f"{BASE_URL}/ping", timeout=2)
            if response.status_code == 200:
                print(f"{Colors.GREEN}✅ Servidor rodando!{Colors.END}\n")
                break
        except:
            if i < 4:
                print(f"  Tentativa {i+1}/5... aguardando 2s")
                time.sleep(2)
            else:
                print(f"\n{Colors.RED}❌ Servidor não está respondendo!{Colors.END}")
                print("Execute: python torrent_stream.py")
                return
    
    # ── TESTE 1: /health ──
    health = test_endpoint(
        "Teste 1: Diagnóstico Completo (/health)",
        "GET",
        "/health"
    )
    if health:
        print(f"\n  Status Geral: {Colors.GREEN}{health['status'].upper()}{Colors.END}")
        print(f"  Mensagem: {health['message']}")
        print(f"  Addons Online: {list(health['addons'].keys())[:2]}")
        print_json(health, max_depth=1)
    
    # ── TESTE 2: /addons/status ──
    status = test_endpoint(
        "Teste 2: Status Rápido (/addons/status)",
        "GET",
        "/addons/status"
    )
    if status:
        print(f"\n  Total de Addons: {len(status['addons'])}")
        for addon in status['addons'][:2]:
            score_bar = "█" * int(addon['score'] / 10) + "░" * (10 - int(addon['score'] / 10))
            print(f"    [{score_bar}] {addon['score']:.1f}/100 - {addon['url'][:40]}...")
    
    # ── TESTE 3: /addons/config (GET) ──
    config = test_endpoint(
        "Teste 3a: Obter Configuração (/addons/config GET)",
        "GET",
        "/addons/config"
    )
    if config:
        print(f"\n  Addons Padrão: {len(config['default_addons'])}")
        print(f"  Addons Customizados: {len(config['custom_addons'])}")
        print(f"  Ativos: {len(config['active_addons'])}")
    
    # ── TESTE 4: /addons/config (POST) ──
    new_config = {
        "addons": [
            "https://torrentio.strem.fun",
        ]
    }
    config_set = test_endpoint(
        "Teste 3b: Definir Configuração (/addons/config POST)",
        "POST",
        "/addons/config",
        data=new_config
    )
    if config_set:
        print(f"\n  Mensagem: {config_set['message']}")
        print(f"  Addons: {config_set['addons']}")
    
    # ── TESTE 5: /addons/config (DELETE) ──
    config_reset = test_endpoint(
        "Teste 3c: Resetar Configuração (/addons/config DELETE)",
        "DELETE",
        "/addons/config"
    )
    if config_reset:
        print(f"\n  Mensagem: {config_reset['message']}")
        print(f"  Addons Padrão Restaurados: {len(config_reset['addons'])}")
    
    # ── TESTE 6: /addons/search ──
    search_result = test_endpoint(
        "Teste 4: Busca de Streams (/addons/search)",
        "GET",
        "/addons/search?name=Jujutsu+Kaisen&season=1&episode=1&nyaa=true"
    )
    if search_result:
        print(f"\n  Streams Encontrados: {search_result['total']}")
        print(f"  Tempo de Busca: {search_result['meta']['duration_ms']:.1f}ms")
        print(f"  Addons Usados: {len(search_result['sources']['addons_used'])}")
        print(f"\n  Recomendações:")
        for i, rec in enumerate(search_result['recommendations'][:2], 1):
            print(f"    {i}. {rec}")
        
        if search_result['streams']:
            print(f"\n  Primeiros 3 Streams:")
            for stream in search_result['streams'][:3]:
                print(f"    • {stream['title'][:60]}...")
                print(f"      Qualidade: {stream['quality']}, Fonte: {stream['source'][:40]}...")
    
    # ── RESUMO ──
    print(f"\n{Colors.BLUE}=" * 60)
    print("✅ Testes Completos!")
    print("=" * 60 + f"{Colors.END}\n")
    
    print("📚 Próximos Passos:")
    print("  1. Veja API_GUIDE.md para documentação completa")
    print("  2. Veja IMPROVEMENTS_SUMMARY.md para exemplos de uso")
    print("  3. Integre com sua aplicação web!\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}↓ Teste cancelado{Colors.END}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.RED}Erro inesperado: {e}{Colors.END}")
        sys.exit(1)
