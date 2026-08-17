#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export SMARTWATCH_CLANK_SOURCE_REVISION="$(git -C "$ROOT" rev-parse HEAD)"
mkdir -p "$ROOT/native/macos/build"
# Stage A deliberately retains a visible console so packaging failures remain
# observable during field testing; the user-facing surface is the browser UI.
exec "${PYTHON:-python3}" -m PyInstaller --noconfirm --clean --paths "$ROOT/src" --name "Smartwatch Clank" --onedir --windowed --specpath "$ROOT/native/macos/build" --distpath "$ROOT/native/macos/dist" --workpath "$ROOT/native/macos/build" --add-data "$ROOT/config:config" "$ROOT/native/macos/launcher.py"
