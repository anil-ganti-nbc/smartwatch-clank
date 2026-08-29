"""RunLock tests.

Covers the OS-level advisory-lock design (fcntl.flock / msvcrt.locking)
that replaced the hostname+PID-liveness check after confirming, against
this repo's own built image, that Docker's one-shot `--rm` containers get
a random hostname per invocation (two real invocations produced
`9b2b3d7c3b9a` and `c9161e1c217f`) and are always PID 1 in their own
namespace -- so a lock left behind by an abnormal exit could never have
been reclaimed by any later invocation under the old design.
"""

import errno
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


class ReadOnlyMountLockTests(unittest.TestCase):
    """Regression coverage for the deploy-script defect: `deploy_hetzner.sh`
    mounts the DB volume read-only for its pre-deploy backup step, but the
    old acquire() unconditionally opened the lock file O_CREAT|O_RDWR --
    which fails with EROFS on a read-only mount even when the lock file
    already exists from a prior writable run, aborting the deploy before
    checkout/build ever ran. Reproduced for real against a genuine Docker
    `:ro` bind mount on a Linux host (see docs/ for the write-up); these
    tests pin the same behavior in a way that runs everywhere, by mocking
    exactly the syscall that fails on a read-only mount rather than trying
    to fake a real read-only filesystem cross-platform.
    """

    @staticmethod
    def _erofs_on_rdwr_open(real_open):
        def _fake_open(path, flags, *args, **kwargs):
            if flags & os.O_CREAT and flags & os.O_RDWR:
                raise OSError(errno.EROFS, "Read-only file system", str(path))
            return real_open(path, flags, *args, **kwargs)
        return _fake_open

    def test_falls_back_to_readonly_open_when_lock_file_already_exists(self):
        """The exact reported scenario: a lock file already exists (from a
        prior real, writable collector/production run against this same
        volume) and the mount is now read-only. acquire() must still
        succeed by falling back to an O_RDONLY open of the same file."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite3"
            # Seed the lock file exactly as a prior writable run would leave it.
            seeded = RunLock(database)
            seeded.acquire()
            seeded.release()

            real_open = os.open
            with mock.patch("smartwatch_clank.core.lock.os.open", side_effect=self._erofs_on_rdwr_open(real_open)):
                readonly_lock = RunLock(database)
                readonly_lock.acquire()
                try:
                    self.assertTrue(readonly_lock.acquired)
                finally:
                    readonly_lock.release()

    def test_clear_error_when_read_only_and_lock_file_never_existed(self):
        """A read-only mount with no pre-existing lock file (e.g. a truly
        fresh volume that has never had a writable run against it) cannot
        be bridged -- the file must genuinely be creatable somewhere first.
        This must raise a clear, actionable RunLockError, not a raw OSError
        traceback."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "never-written.sqlite3"
            real_open = os.open
            with mock.patch("smartwatch_clank.core.lock.os.open", side_effect=self._erofs_on_rdwr_open(real_open)):
                with self.assertRaisesRegex(RunLockError, "read-only mount"):
                    RunLock(database).acquire()

    @unittest.skipIf(sys.platform == "win32", "flock()/O_RDONLY semantics under test are POSIX-specific")
    def test_readonly_fallback_lock_genuinely_conflicts_with_a_real_writer(self):
        """Proves the core safety property the fix depends on: an
        O_RDONLY-opened flock() is not a second-class lock -- it
        participates in the exact same mutual exclusion as a normal
        O_RDWR-opened one, on the same underlying file, regardless of which
        side opened it which way. Verified for real against a Docker `:ro`
        bind mount on Linux; this pins the same guarantee locally without
        Docker, using the identical fcntl.flock() call the production code
        path uses."""
        import fcntl

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite3"
            writer = RunLock(database)
            writer.acquire()
            try:
                readonly_fd = os.open(writer.path, os.O_RDONLY)
                try:
                    with self.assertRaises(OSError):
                        fcntl.flock(readonly_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(readonly_fd)
            finally:
                writer.release()

            # And the reverse direction: a lock taken through an
            # O_RDONLY-opened fd must equally block a real O_RDWR writer.
            readonly_fd = os.open(database.with_suffix(database.suffix + ".lock"), os.O_RDONLY)
            fcntl.flock(readonly_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(RunLockError):
                    RunLock(database).acquire()
            finally:
                fcntl.flock(readonly_fd, fcntl.LOCK_UN)
                os.close(readonly_fd)


if __name__ == "__main__":
    unittest.main()
