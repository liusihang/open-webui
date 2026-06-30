from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - msvcrt is only available on Windows.
    msvcrt = None


_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _default_lock_dir() -> Path:
    from open_webui.env import DATA_DIR

    return Path(DATA_DIR) / "startup-locks"


def _safe_lock_name(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("-", name).strip(".-")
    return cleaned or "startup-singleton"


class StartupSingletonLock:
    def __init__(
        self,
        name: str,
        *,
        lock_dir: str | Path | None = None,
        blocking: bool = False,
    ) -> None:
        self.name = _safe_lock_name(name)
        self.lock_dir = Path(lock_dir) if lock_dir is not None else _default_lock_dir()
        self.lock_path = self.lock_dir / f"{self.name}.lock"
        self.blocking = blocking
        self.acquired = False
        self._file: TextIO | None = None

    def acquire(self, *, blocking: bool | None = None) -> bool:
        if self.acquired:
            return True

        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+")
        should_block = self.blocking if blocking is None else blocking

        if not self._acquire_file_lock(lock_file, blocking=should_block):
            lock_file.close()
            return False

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.flush()

        self._file = lock_file
        self.acquired = True
        return True

    def release(self) -> None:
        if not self._file:
            self.acquired = False
            return

        try:
            self._release_file_lock(self._file)
        finally:
            self._file.close()
            self._file = None
            self.acquired = False

    def _acquire_file_lock(self, lock_file: TextIO, *, blocking: bool) -> bool:
        if fcntl is not None:
            lock_flags = fcntl.LOCK_EX
            if not blocking:
                lock_flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(lock_file.fileno(), lock_flags)
            except BlockingIOError:
                return False
            return True

        if msvcrt is not None:
            while True:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    return True
                except OSError:
                    if not blocking:
                        return False
                    time.sleep(0.1)

        raise RuntimeError("Startup singleton locks require fcntl or msvcrt")

    def _release_file_lock(self, lock_file: TextIO) -> None:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    def __enter__(self) -> "StartupSingletonLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def startup_singleton_lock(
    name: str,
    *,
    lock_dir: str | Path | None = None,
    blocking: bool = False,
) -> StartupSingletonLock:
    return StartupSingletonLock(name, lock_dir=lock_dir, blocking=blocking)
