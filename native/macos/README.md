# Smartwatch Clank macOS field test

The thin launcher runs the canonical core and local loopback dashboard. It uses `SMARTWATCH_CLANK_DATA_DIR`, defaulting to `~/Library/Application Support/Smartwatch Clank`, without a hard-coded username. It does not bundle secrets or SQLite state.

Build with `PYTHON="$(pwd)/.venv-codex/bin/python" native/macos/build.sh` and open `native/macos/dist/Smartwatch Clank/Smartwatch Clank`.
