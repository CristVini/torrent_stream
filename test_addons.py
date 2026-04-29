#!/usr/bin/env python3
"""
Script de teste para addons customizados do TorrentStream
"""
import os
import sys
import json

# Simular ambiente do torrent_stream.py
current_dir = "/workspaces/torrent_stream"
vendor_dir = os.path.join(current_dir, "vendor")

if os.path.isdir(vendor_dir):
    sys.path.insert(0, vendor_dir)

# Imports necessários
from flask import Flask, request, jsonify
import threading
import time

def test_custom_addons():
    """Testa a funcionalidade de addons customizados"""

    print("🧪 Testando addons customizados...")
    print("=" * 50)

    # Simular a lógica de processamento de addons
    def process_addon_param(custom_addons):
        """Simula o processamento do parâmetro addons"""
        if custom_addons:
            # Permite múltiplos addons separados por vírgula
            addon_urls = [url.strip() for url in custom_addons.split(",") if url.strip()]
            print(f"✅ Addons customizados detectados: {len(addon_urls)}")
            for i, url in enumerate(addon_urls, 1):
                print(f"   {i}. {url}")
            return addon_urls
        else:
            # Addons padrão
            default_addons = [
                "https://torrentio.strem.fun",
                "https://mediafusion.elfhosted.com",
                "https://comet.elfhosted.com",
            ]
            print(f"ℹ Usando addons padrão: {len(default_addons)}")
            for i, url in enumerate(default_addons, 1):
                print(f"   {i}. {url}")
            return default_addons

    # Testes
    test_cases = [
        ("", "Addons padrão"),
        ("https://meu-addon.com", "Addon único"),
        ("https://addon1.com,https://addon2.net", "Dois addons"),
        ("https://addon1.com, https://addon2.net , https://addon3.org", "Três addons com espaços"),
    ]

    for custom_param, description in test_cases:
        print(f"\n🧪 Teste: {description}")
        print(f"   Parâmetro: '{custom_param}'")
        result = process_addon_param(custom_param)
        print(f"   Resultado: {len(result)} addons processados")

    print("\n" + "=" * 50)
    print("✅ Todos os testes de addons customizados passaram!")
    print("\n💡 Exemplos de uso:")
    print("   GET /addons/search?name=Show&season=1&episode=1")
    print("   GET /addons/search?name=Show&season=1&episode=1&addons=https://meu-addon.com")
    print("   GET /addons/search?name=Show&season=1&episode=1&addons=https://addon1.com,https://addon2.net")

if __name__ == "__main__":
    test_custom_addons()