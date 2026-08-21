from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path


class RunLockError(RuntimeError):
    pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class RunLock:
    def __init__(self, database: Path | str) -> None:
        self.database = Path(database).resolve()
        self.path = self.database.with_suffix(self.database.suffix + ".lock")
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        owner = {
            "pid": os.getpid(), "host": socket.gethostname(), "database": str(self.database),
            "acquired_at": datetime.now(timezone.utc).isoformat(), "token": self.token,
        }
        for attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 0 and self._reclaim_stale_local_lock():
                    continue
                current = self._read_owner()
                raise RunLockError(f"database run lock is held: {json.dumps(current, sort_keys=True)}")
            try:
                os.write(descriptor, json.dumps(owner, sort_keys=True).encode("utf-8"))
            finally:
                os.close(descriptor)
            self.acquired = True
            return
        raise RunLockError(f"could not acquire database run lock: {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        owner = self._read_owner()
        if owner.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def _read_owner(self) -> dict[str, object]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"path": str(self.path), "owner": "unreadable"}

    def _reclaim_stale_local_lock(self) -> bool:
        owner = self._read_owner()
        if owner.get("host") != socket.gethostname():
            return False
        try:
            pid = int(owner["pid"])
        except (KeyError, TypeError, ValueError):
            return False
        if _pid_is_alive(pid):
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
