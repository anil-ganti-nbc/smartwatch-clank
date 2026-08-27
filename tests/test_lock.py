"""RunLock tests.

Covers the OS-level advisory-lock design (fcntl.flock / msvcrt.locking)
that replaced the hostname+PID-liveness check after confirming, against
this repo's own built image, that Docker's one-shot `--rm` containers get
a random hostname per invocation (two real invocations produced
`9b2b3d7c3b9a` and `c9161e1c217f`) and are always PID 1 in their own
namespace -- so a lock left behind by an abnormal exit could never have
been reclaimed by any later invocation under the old design.
"""

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartwatch_clank.core.lock import RunLock, RunLockError, _pid_is_alive


class RunLockTests(unittest.TestCase):
    def test_acquire_release_and_reacquire(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite3"
            first = RunLock(database)
            first.acquire()
            self.assertTrue(first.path.exists())
            with self.assertRaises(RunLockError):
                RunLock(database).acquire()
            first.release()
            # The lock file is deliberately NOT deleted on release --
            # unlinking it here would race a concurrent acquirer that
            # already opened the old path (classic flock-then-unlink
            # hazard). What matters is the lock is genuinely releasable
            # and immediately re-acquirable.
            self.assertTrue(first.path.exists())
            second = RunLock(database)
            second.acquire()
            second.release()

    def test_second_acquisition_genuinely_blocked_while_held(self):
        """Two independent RunLock instances on the same path -- exactly
        what two separate one-shot containers each opening their own fd on
        the shared, bind-mounted lock file would do -- create two separate
        open file descriptions. flock()/LockFileEx() ties the lock to the
        open file description, not the process, so the second acquire
        correctly fails even within a single test process."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite3"
            first = RunLock(database)
            first.acquire()
            try:
                with self.assertRaises(RunLockError):
                    RunLock(database).acquire()
            finally:
                first.release()

    def test_stale_lock_from_different_host_and_pid_1_recovers_immediately(self):
        """Reproduces the exact confirmed bug and proves the fix.

        A lock file is seeded exactly like a crashed container would leave
        one: a *different* hostname (as every separate one-shot container
        genuinely has) and pid=1 (as every container's own main process
        genuinely is). Under the old design, `_reclaim_stale_local_lock`
        checked hostname first and refused outright on mismatch -- this
        state would have blocked every future invocation forever. The new
        design ignores host/pid entirely and recovers immediately because
        nothing holds the OS lock.
        """
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(Path(directory) / "live.sqlite3")
            lock.path.write_text(
                json.dumps({
                    "pid": 1,
                    "host": "9b2b3d7c3b9a",  # a real container hostname seen in testing, guaranteed not to match this process's
                    "token": "dead-container",
                }),
                encoding="utf-8",
            )
            # Document why the old check would have refused forever: this
            # host will never equal a container's random hostname.
            self.assertNotEqual("9b2b3d7c3b9a", socket.gethostname())

            with mock.patch("smartwatch_clank.core.lock.os.getpid", return_value=1):
                lock.acquire()
                self.assertTrue(lock.acquired)

            owner = json.loads(lock.path.read_text(encoding="utf-8"))
            self.assertEqual(owner["pid"], 1)
            self.assertNotEqual(owner["token"], "dead-container")
            lock.release()
            self.assertFalse(lock.acquired)

    def test_stale_lock_content_is_diagnostic_only_not_a_gate(self):
        """A completely unreadable/corrupt lock file must not block
        recovery either -- content is never load-bearing for the decision
        to acquire, only for a friendlier error message when the lock
        genuinely is held."""
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(Path(directory) / "live.sqlite3")
            lock.path.write_text("not json at all {{{", encoding="utf-8")
            lock.acquire()
            self.assertTrue(lock.acquired)
            lock.release()

    def test_exception_path_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(Path(directory) / "live.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with lock:
                    raise RuntimeError("boom")
            self.assertFalse(lock.acquired)
            # re-acquiring immediately proves it was genuinely released by
            # __exit__ even on the exception path, not just that the file
            # still exists
            second = RunLock(lock.database)
            second.acquire()
            second.release()

    def test_release_is_idempotent_and_survives_external_file_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(Path(directory) / "live.sqlite3")
            lock.acquire()
            if sys.platform != "win32":
                # POSIX allows unlinking a file a process still has open;
                # Windows file-sharing semantics don't permit deleting an
                # open file at all, so this specific scenario is
                # POSIX-only -- the idempotency assertions below still run
                # on every platform.
                lock.path.unlink()
            lock.release()
            self.assertFalse(lock.acquired)
            lock.release()  # calling release() twice must be a no-op

    def test_pid_is_alive_self(self):
        self.assertTrue(_pid_is_alive(os.getpid()))


if __name__ == "__main__":
    unittest.main()
