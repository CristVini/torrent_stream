#!/usr/bin/env python3
"""
Setup script para configurar FFmpeg no projeto.
- Windows: baixa FFmpeg estático (BtbN)
- Linux/Mac: cria symlinks para FFmpeg do sistema ou cria wrappers
"""

import os
import sys
import urllib.request
import zipfile
import shutil
import platform
import subprocess

def find_ffmpeg_in_path():
    """Procura por ffmpeg e ffprobe no PATH do sistema."""
    import shutil
    ffmpeg_path = shutil.which('ffmpeg')
    ffprobe_path = shutil.which('ffprobe')
    return ffmpeg_path, ffprobe_path

def create_wrapper(src_path, dst_path):
    """Cria um wrapper script que chama o ffmpeg do sistema."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    
    with open(dst_path, 'w') as f:
        f.write(f"""#!/bin/bash
exec "{src_path}" "$@"
""")
    os.chmod(dst_path, 0o755)

def create_symlink(src_path, dst_path):
    """Cria um symlink para o ffmpeg do sistema."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    
    # Se já existe, remove
    if os.path.exists(dst_path) or os.path.islink(dst_path):
        try:
            os.remove(dst_path)
        except:
            pass
    
    try:
        os.symlink(src_path, dst_path)
        return True
    except:
        return False

def download_ffmpeg():
    """Baixa FFmpeg estático e o inclui no projeto."""
    
    project_root = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_dir = os.path.join(project_root, 'ffmpeg_bin')
    
    os.makedirs(ffmpeg_dir, exist_ok=True)
    
    # Detectar sistema operacional
    system = platform.system()
    
    if system == 'Windows':
        print("⬇️ Baixando FFmpeg para Windows...")
        url = (
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
            "ffmpeg-master-latest-win64-gpl.zip"
        )
        zip_path = os.path.join(ffmpeg_dir, 'ffmpeg.zip')
        
        try:
            print(f"   URL: {url}")
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'TorrentStream/3.2.0'}
            )
            print(f"   Salvando em: {zip_path}")
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get('Content-Length', 0))
                downloaded = 0
                chunk_size = 256 * 1024
                
                with open(zip_path, 'wb') as out:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        pct = (downloaded / total * 100) if total > 0 else 0
                        print(f"   {pct:.1f}% ({downloaded/1024/1024:.1f}MB)", end='\r')
            
            print(f"\n✅ Download concluído")
            
            # Extrair
            print("📦 Extraindo FFmpeg...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    basename = os.path.basename(name)
                    if basename in ('ffmpeg.exe', 'ffprobe.exe') and '/bin/' in name:
                        dest = os.path.join(ffmpeg_dir, basename)
                        print(f"   Extraindo: {basename}")
                        with zf.open(name) as src, open(dest, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
            
            os.remove(zip_path)
            print(f"✅ FFmpeg extraído em: {ffmpeg_dir}")
            return True
            
        except Exception as e:
            print(f"❌ Erro: {e}")
            try:
                os.remove(zip_path)
            except:
                pass
            return False
    
    elif system in ('Linux', 'Darwin'):
        print(f"🔍 Procurando FFmpeg no sistema ({system})...")
        ffmpeg_path, ffprobe_path = find_ffmpeg_in_path()
        
        if ffmpeg_path and ffprobe_path:
            print(f"   ✅ ffmpeg encontrado em: {ffmpeg_path}")
            print(f"   ✅ ffprobe encontrado em: {ffprobe_path}")
            
            # Criar symlinks
            print("\n🔗 Criando symlinks...")
            ffmpeg_link = os.path.join(ffmpeg_dir, 'ffmpeg')
            ffprobe_link = os.path.join(ffmpeg_dir, 'ffprobe')
            
            ok1 = create_symlink(ffmpeg_path, ffmpeg_link)
            ok2 = create_symlink(ffprobe_path, ffprobe_link)
            
            if ok1 and ok2:
                print(f"   ✅ Symlinks criados em: {ffmpeg_dir}")
                return True
            else:
                # Se symlinks falharem, tentar wrappers
                print("   ⚠️  Symlinks falharam, criando wrappers...")
                create_wrapper(ffmpeg_path, ffmpeg_link)
                create_wrapper(ffprobe_path, ffprobe_link)
                print(f"   ✅ Wrappers criados em: {ffmpeg_dir}")
                return True
        else:
            print("   ❌ FFmpeg não encontrado no PATH")
            print(f"\n⚠️  Instale FFmpeg:")
            if system == 'Linux':
                print("      apt-get install -y ffmpeg")
            else:
                print("      brew install ffmpeg")
            return False
    
    else:
        print(f"⚠️  Sistema {system} não suportado")
        return False

if __name__ == '__main__':
    print("🎬 TorrentStream - Setup FFmpeg")
    print("=" * 50)
    
    if download_ffmpeg():
        print("\n✅ Setup concluído com sucesso!")
        sys.exit(0)
    else:
        print("\n⚠️  Setup incompleto (FFmpeg pode precisar ser instalado manualmente)")
        sys.exit(1)

