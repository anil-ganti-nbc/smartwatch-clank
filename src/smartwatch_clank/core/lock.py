"""Cross-platform single-instance database run lock.

Uses an OS-level advisory file lock (`fcntl.flock` on POSIX, `msvcrt.locking`
on Windows) instead of a hostname+PID-liveness check. This is a deliberate
fix for a proven failure mode, confirmed empirically against this repo's own
built image: in the one-shot `docker compose run --rm` model, every
container gets Docker's default *random* container-ID hostname (two
separate invocations of the same image produced `9b2b3d7c3b9a` and
`c9161e1c217f`) and its main process is always PID 1 in its own private PID
namespace. The old `_reclaim_stale_local_lock()` checked `owner["host"] !=
socket.gethostname()` *before* even looking at the PID -- since the hostname
essentially never matches across separate container invocations, that check
alone meant a lock left behind by any abnormal exit could never be reclaimed
by any later invocation, ever. (Had the hostname check somehow passed, the
PID check would have failed identically to OEM Radar's proven pid=1 bug --
see Diagnostic Clank incident 5f280abf -- since every container's own
process is also PID 1.)

An OS lock sidesteps the whole problem: the kernel ties the lock to the lock
file's inode, which is genuinely shared across containers via the
bind-mounted/volume-backed lock file, and the kernel releases the lock
automatically when the holding process's file descriptor closes -- for any
reason, including a crash or an OOM-kill. No hostname, no PID, no liveness
check, and no staleness window is needed at all to decide whether the lock
can be acquired.

Ported from the same fleet-proven design already used by Free Game Tracker
(newsroom/run_lock.py) and OEM Radar (core/run_lock.py, PR #4) rather than
inventing a new mechanism, adapted to this class's exact constructor/
acquire()/release()/context-manager surface so `cli.py` and
`local_collection.py` need no changes.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_WINDOWS_LOCK_OFFSET = 1 << 20


class RunLockError(RuntimeError):
    pass


def _pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check, retained for diagnostics only.

    NEVER consulted to decide whether the lock can be acquired -- see the
    module docstring for why a hostname/PID-liveness check is fundamentally
    unsound across one-shot Docker containers. Exists only to make a
    RunLockError's message more informative when metadata happens to be
    readable.
    """
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


def _os_lock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


class RunLock:
    def __init__(self, database: Path | str) -> None:
        self.database = Path(database).resolve()
        self.path = self.database.with_suffix(self.database.suffix + ".lock")
        self.token = uuid.uuid4().hex
        self.acquired = False
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        try:
            _os_lock(fd)
        except OSError as exc:
            current = self._read_owner()
            os.close(fd)
            raise RunLockError(
                f"database run lock is held: {json.dumps(current, sort_keys=True)}"
            ) from exc

        owner = {
            "pid": os.getpid(), "host": socket.gethostname(), "database": str(self.database),
            "acquired_at": datetime.now(timezone.utc).isoformat(), "token": self.token,
        }
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, json.dumps(owner, sort_keys=True).encode("utf-8"))
        except OSError:
            # Metadata is diagnostic-only; a write failure must not stop us
            # from holding a lock we have already, genuinely, acquired.
            pass
        self._fd = fd
        self.acquired = True

    def release(self) -> None:
        if not self.acquired or self._fd is None:
            return
        try:
            _os_unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None
            self.acquired = False

    def _read_owner(self) -> dict[str, object]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"path": str(self.path), "owner": "unreadable"}
