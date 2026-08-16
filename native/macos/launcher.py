from __future__ import annotations
import os, socket, sys, threading, webbrowser
from pathlib import Path

def main() -> None:
    data = Path(os.environ.setdefault("SMARTWATCH_CLANK_DATA_DIR", str(Path.home() / "Library" / "Application Support" / "Smartwatch Clank"))).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    # PyInstaller places bundled config under its resource root; source mode
    # continues to use the repository root without this override.
    if hasattr(sys, "_MEIPASS"):
        app_resources = Path(sys.executable).resolve().parents[3] / "Smartwatch Clank" / "_internal"
        os.environ.setdefault("SMARTWATCH_CLANK_CONFIG_ROOT", str(app_resources if app_resources.exists() else sys._MEIPASS))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    from smartwatch_clank.dashboard import serve
    server = serve(port=port)
    threading.Timer(.25, webbrowser.open, args=(f"http://127.0.0.1:{port}/",)).start()
    server.serve_forever()
if __name__ == "__main__": main()
