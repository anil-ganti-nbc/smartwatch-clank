"""Windows symmetry launcher -- the supported local-operator entry point.

Mirrors `native/macos/launcher.py` exactly (same canonical core, same
loopback dashboard, same `SMARTWATCH_CLANK_DATA_DIR` boundary -- defaulting
here to `%LOCALAPPDATA%\\Smartwatch Clank` per `native/windows/README.md`),
with one deliberate addition: this is the ONLY place in the repository that
passes `local_operator=True` to `dashboard.serve()`, which is what narrowly
lifts the Phase-0 dashboard-mutation freeze for a real local desktop
session -- see `smartwatch_clank/local_operator.py` for the full safety
rationale (loopback re-proven per request, closed-ended route allowlist,
no notification path touched).

Run directly with `python native/windows/launcher.py` (no PyInstaller
bundling required for local use); a bundled `.exe` build can wrap this same
entry point without changing its behaviour.
"""
from __future__ import annotations
import os, socket, sys, threading, webbrowser, time, urllib.request
from pathlib import Path


def main() -> None:
    default_home = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Smartwatch Clank"
    data = Path(os.environ.get("SMARTWATCH_FIELD_TEST_HOME") or default_home).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.environ["SMARTWATCH_CLANK_DATA_DIR"] = str(data)
    os.environ.pop("SMARTWATCH_CLANK_DB", None)
    # PyInstaller places bundled config under its resource root; source mode
    # continues to use the repository root without this override.
    if hasattr(sys, "_MEIPASS"):
        os.environ["SMARTWATCH_CLANK_CONFIG_ROOT"] = str(Path(sys._MEIPASS).resolve())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    from smartwatch_clank.configuration import load_runtime_config
    from smartwatch_clank.collectors import default_registry
    from smartwatch_clank.local_collection import LocalCollectionController
    from smartwatch_clank.dashboard import serve
    controller = LocalCollectionController(load_runtime_config(), default_registry())
    # local_operator=True: this launcher IS the "authenticated profile" --
    # a deliberate, supported, loopback-only local desktop session. No
    # collector runs yet; the GUI has not even opened a socket to a client.
    server = serve(port=port, controller=controller, local_operator=True)
    url = f"http://127.0.0.1:{port}/"

    def ready():
        for _ in range(200):
            try:
                if urllib.request.urlopen(url + "healthz", timeout=1).status == 200:
                    webbrowser.open(url); return
            except Exception:
                time.sleep(.15)

    threading.Thread(target=ready, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
