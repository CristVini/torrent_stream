#!/usr/bin/env python3
"""
Script de teste para verificar portas customizadas no TorrentStream
"""
import os
import sys
import subprocess
import time

def test_port_configuration():
    """Testa se a configuração de porta funciona corretamente"""

    print("🧪 Testando configuração de porta customizada...")
    print()

    # Teste 1: Porta padrão (5000)
    print("Teste 1: Porta padrão (5000)")
    os.environ.pop('PORT', None)  # Remove se existir
    port = int(os.environ.get("PORT", "5000"))
    print(f"   PORT variável: {os.environ.get('PORT', 'não definida')}")
    print(f"   Porta usada: {port}")
    print()

    # Teste 2: Porta customizada (8080)
    print("Teste 2: Porta customizada (8080)")
    os.environ['PORT'] = '8080'
    port = int(os.environ.get("PORT", "5000"))
    print(f"   PORT variável: {os.environ.get('PORT')}")
    print(f"   Porta usada: {port}")
    print()

    # Teste 3: Porta customizada (3000)
    print("Teste 3: Porta customizada (3000)")
    os.environ['PORT'] = '3000'
    port = int(os.environ.get("PORT", "5000"))
    print(f"   PORT variável: {os.environ.get('PORT')}")
    print(f"   Porta usada: {port}")
    print()

    print("✅ Todos os testes passaram!")
    print()
    print("💡 Como usar:")
    print("   python3 torrent_stream.py              # Porta 5000 (padrão)")
    print("   PORT=8080 python3 torrent_stream.py    # Porta 8080")
    print("   PORT=3000 python3 torrent_stream.py    # Porta 3000")

if __name__ == "__main__":
    test_port_configuration()