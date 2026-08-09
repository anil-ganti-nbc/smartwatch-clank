import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.core.lock import RunLock, RunLockError


class RunLockTests(unittest.TestCase):
    def test_acquire_release_and_second_acquisition_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite3"
            first = RunLock(database)
            first.acquire()
            self.assertTrue(first.path.exists())
            with self.assertRaises(RunLockError):
                RunLock(database).acquire()
            first.release()
            self.assertFalse(first.path.exists())

    def test_stale_dead_local_owner_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(Path(directory) / "live.sqlite3")
            lock.path.write_text(json.dumps({"pid": 99999999, "host": socket.gethostname(), "token": "dead"}), encoding="utf-8")
            lock.acquire()
            owner = json.loads(lock.path.read_text(encoding="utf-8"))
            self.assertEqual(owner["pid"], os.getpid())
            lock.release()

    def test_exception_path_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(Path(directory) / "live.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with lock:
                    raise RuntimeError("boom")
            self.assertFalse(lock.path.exists())


if __name__ == "__main__":
    unittest.main()

