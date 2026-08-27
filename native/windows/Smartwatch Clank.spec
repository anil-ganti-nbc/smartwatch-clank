# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parents[1]

# Unlike watch-clank's macOS bundle, this dashboard is a plain
# http.server-based app (see src/smartwatch_clank/dashboard.py) -- no
# FastAPI/uvicorn and no Jinja templates directory anywhere in this repo
# (pyproject.toml's dependencies are deliberately empty). What it DOES need
# bundled is `config/` (config.yaml + scope.yaml, read via
# smartwatch_clank.paths.config_path()/PROJECT_ROOT, which the launcher
# repoints at sys._MEIPASS when frozen -- see native/windows/launcher.py).
a = Analysis(
    [str(root / "native" / "windows" / "launcher.py")],
    pathex=[str(root), str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "config"), "config"),
    ],
    hiddenimports=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Smartwatch Clank",
    console=False,
)
