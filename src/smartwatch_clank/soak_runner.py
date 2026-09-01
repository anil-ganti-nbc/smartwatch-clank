from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .configuration import load_runtime_config


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write(log, message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def main() -> int:
    root = _project_root()
    default_logs = root / "var" / "logs" / "soak"
    log_directory = Path(os.environ.get("SMARTWATCH_CLANK_SOAK_LOG_DIR", default_logs))
    log_directory.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    for item in log_directory.glob("smartwatch-clank-*.log"):
        if datetime.fromtimestamp(item.stat().st_mtime, timezone.utc) < cutoff:
            item.unlink()
    log_path = log_directory / f"smartwatch-clank-{datetime.now().date().isoformat()}.log"
    # This tracked scheduler launcher is the authority that can assert a
    # scheduled trigger. Direct CLI/library callers remain MANUAL/UNKNOWN.
    command = [sys.executable, "-m", "smartwatch_clank.cli", "run",
               "--mode", "production", "--trigger", "SCHEDULED"]
    started = datetime.now(timezone.utc)
    database = load_runtime_config().database.resolve()
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        _write(log, f'cycle_start mode=production database="{database}" pid={os.getpid()}')
        try:
            process = subprocess.Popen(
                command, cwd=root, env=os.environ.copy(), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
            )
            assert process.stdout is not None
            for output in process.stdout:
                _write(log, "cli " + output.rstrip("\r\n"))
            status = process.wait()
        except Exception as exc:
            _write(log, f"runner_exception error={type(exc).__name__}: {exc}")
            status = 1
        duration = (datetime.now(timezone.utc) - started).total_seconds()
        _write(log, f"cycle_finish exit_status={status} duration_seconds={duration:.3f}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
