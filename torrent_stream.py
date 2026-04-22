import libtorrent as lt
import time
import os
import sys
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from flask import Flask, request, Response, jsonify
from flask_cors import CORS

# ── Config persistente ────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(sys.executable), "torrent_stream_config.txt")

def load_download_path():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            path = f.read().strip()
            if os.path.exists(path):
                return path
    return os.path.join(os.environ.get("TEMP", "C:\\Temp"), "TorrentStream")

def save_download_path(path):
    with open(CONFIG_FILE, "w") as f:
        f.write(path)

# ── Janela de configuração ────────────────────────────────────────────────────
def show_config_window():
    root = tk.Tk()
    root.title("TorrentStream - Configuração")
    root.geometry("520x320")
    root.resizable(False, False)
    root.configure(bg="#1e1e2e")

    selected_path = tk.StringVar(value=load_download_path())
    temp_var = tk.BooleanVar(value=True)  # padrão: usar pasta temporária
    result = {"start": False}

    # Título
    tk.Label(root, text="🎬 TorrentStream", font=("Segoe UI", 18, "bold"),
             bg="#1e1e2e", fg="#cdd6f4").pack(pady=(20, 4))
    tk.Label(root, text="Servidor de streaming via torrent",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#a6adc8").pack()

    # Checkbox temporário
    chk_frame = tk.Frame(root, bg="#1e1e2e")
    chk_frame.pack(anchor="w", padx=30, pady=(16, 0))
    tk.Checkbutton(
        chk_frame, text="Usar pasta temporária (deletar arquivos ao fechar)",
        variable=temp_var, bg="#1e1e2e", fg="#a6e3a1", selectcolor="#313244",
        activebackground="#1e1e2e", activeforeground="#a6e3a1",
        font=("Segoe UI", 9), cursor="hand2",
        command=lambda: entry.config(state="disabled" if temp_var.get() else "normal")
    ).pack()

    # Pasta de download
    tk.Label(root, text="Ou escolha uma pasta permanente:",
             font=("Segoe UI", 10), bg="#1e1e2e", fg="#cdd6f4").pack(anchor="w", padx=30, pady=(12, 4))

    frame = tk.Frame(root, bg="#1e1e2e")
    frame.pack(fill="x", padx=30)

    entry = tk.Entry(frame, textvariable=selected_path, font=("Segoe UI", 9),
                     bg="#313244", fg="#cdd6f4", insertbackground="white",
                     relief="flat", bd=6, state="disabled")
    entry.pack(side="left", fill="x", expand=True)

    def browse():
        path = filedialog.askdirectory(title="Escolha a pasta de download")
        if path:
            selected_path.set(path)
            temp_var.set(False)
            entry.config(state="normal")

    tk.Button(frame, text="📂", command=browse, bg="#45475a", fg="#cdd6f4",
              relief="flat", font=("Segoe UI", 10), cursor="hand2",
              padx=8).pack(side="left", padx=(6, 0))

    # Botões
    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(pady=20)

    def start():
        if temp_var.get():
            path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "TorrentStream")
        else:
            path = selected_path.get().strip()
            if not path:
                messagebox.showwarning("Atenção", "Escolha uma pasta de download.")
                return
            save_download_path(path)

        os.makedirs(path, exist_ok=True)
        result["start"] = True
        result["path"] = path
        result["temporary"] = temp_var.get()
        root.destroy()

    def uninstall():
        if messagebox.askyesno(
            "Desinstalar",
            "Isso vai deletar:\n\n"
            "• A pasta de downloads\n"
            "• O arquivo de configuração\n"
            "• O próprio executável\n\n"
            "Tem certeza?"
        ):
            dl_path = selected_path.get().strip()
            if os.path.exists(dl_path):
                shutil.rmtree(dl_path, ignore_errors=True)
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            exe = sys.executable
            bat = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "uninstall_ts.bat")
            with open(bat, "w") as f:
                f.write('@echo off\n')
                f.write('timeout /t 2 /nobreak >nul\n')
                f.write(f'del /f /q "{exe}"\n')
                f.write('del /f /q "%~f0"\n')
            os.startfile(bat)
            messagebox.showinfo("Pronto", "TorrentStream removido com sucesso!")
            root.destroy()
            sys.exit(0)

    tk.Button(btn_frame, text="▶  Iniciar Servidor", command=start,
              bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
              relief="flat", cursor="hand2", padx=20, pady=8).pack(side="left", padx=8)

    tk.Button(btn_frame, text="🗑  Desinstalar", command=uninstall,
              bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
              relief="flat", cursor="hand2", padx=20, pady=8).pack(side="left", padx=8)

    root.mainloop()
    return result

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

ses = lt.session()
ses.listen_on(6881, 6891)

DOWNLOAD_PATH = ""
IS_TEMPORARY = True
active_handles = {}  # magnet -> handle

def cleanup_torrent(handle, file_path):
    """Remove o torrent da sessão e deleta o arquivo se modo temporário."""
    try:
        ses.remove_torrent(handle)
    except Exception:
        pass
    if IS_TEMPORARY and os.path.exists(file_path):
        try:
            # Tenta deletar o arquivo
            os.remove(file_path)
            print(f"🗑 Deletado: {file_path}")
            # Remove pasta vazia
            folder = os.path.dirname(file_path)
            if os.path.isdir(folder) and not os.listdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
        except Exception as e:
            print(f"Erro ao deletar: {e}")

def cleanup_all():
    """Deleta toda a pasta temporária ao encerrar."""
    if IS_TEMPORARY and os.path.exists(DOWNLOAD_PATH):
        shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
        print(f"🗑 Pasta temporária deletada: {DOWNLOAD_PATH}")

@app.route("/play")
def play():
    magnet = request.args.get("magnet")
    if not magnet:
        return jsonify({"error": "No magnet link provided"}), 400

    params = {
        'save_path': DOWNLOAD_PATH,
        'storage_mode': lt.storage_mode_t(2),
    }

    handle = lt.add_magnet_uri(ses, magnet, params)
    print("Baixando metadata...")

    timeout = 60
    elapsed = 0
    while not handle.has_metadata():
        time.sleep(1)
        elapsed += 1
        if elapsed >= timeout:
            return jsonify({"error": "Timeout ao buscar metadata"}), 504

    torrent_info = handle.get_torrent_info()
    files = torrent_info.files()

    file_index = max(range(files.num_files()), key=lambda i: files.file_size(i))

    for i in range(files.num_files()):
        handle.file_priority(i, 0)
    handle.file_priority(file_index, 7)

    file_path = os.path.join(DOWNLOAD_PATH, files.file_path(file_index))
    file_size = files.file_size(file_index)

    print(f"Arquivo: {file_path} ({file_size / 1024 / 1024:.1f} MB)")

    def generate():
        print("Aguardando buffer mínimo (5%)...")
        while True:
            s = handle.status()
            if s.progress > 0.05:
                break
            time.sleep(1)

        print("Iniciando stream...")
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            # Stream encerrado (usuário fechou o player) → limpa
            print("Stream encerrado, limpando...")
            threading.Thread(
                target=cleanup_torrent, args=(handle, file_path), daemon=True
            ).start()

    ext = os.path.splitext(file_path)[1].lower()
    content_types = {
        '.mp4': 'video/mp4', '.mkv': 'video/x-matroska',
        '.avi': 'video/x-msvideo', '.webm': 'video/webm', '.mov': 'video/quicktime',
    }

    return Response(
        generate(),
        content_type=content_types.get(ext, 'video/mp4'),
        headers={
            'Content-Length': str(file_size),
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*',
        }
    )


@app.route("/status")
def status():
    torrents = ses.get_torrents()
    result = []
    for h in torrents:
        s = h.status()
        result.append({
            'name': s.name,
            'progress': round(s.progress * 100, 1),
            'progress_str': f"{s.progress * 100:.1f}%",
            'download_rate_kbps': round(s.download_rate / 1024, 1),
            'download_rate_str': f"{s.download_rate / 1024:.1f} KB/s",
            'peers': s.num_peers,
            'state': str(s.state),
        })
    return jsonify({
        "torrents": result,
        "download_path": DOWNLOAD_PATH,
        "temporary": IS_TEMPORARY,
    })


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = show_config_window()

    if not result.get("start"):
        sys.exit(0)

    DOWNLOAD_PATH = result["path"]
    IS_TEMPORARY = result["temporary"]

    mode = "temporário (auto-delete)" if IS_TEMPORARY else "permanente"
    print(f"📁 Salvando em: {DOWNLOAD_PATH} [{mode}]")
    print(f"🚀 Servidor rodando em http://0.0.0.0:5000")

    try:
        app.run(host="0.0.0.0", port=5000, threaded=True)
    finally:
        cleanup_all()