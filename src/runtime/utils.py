"""Shared utility functions for the Gazer core."""

import json
import logging
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger("GazerUtils")

# ---------------------------------------------------------------------------
# Cross-platform file locking
# ---------------------------------------------------------------------------


class FileLockError(Exception):
    """Raised when a file lock cannot be acquired."""
    pass


class FileLock:
    """Cross-platform file lock using fcntl (Unix) or msvcrt (Windows).
    
    Usage:
        with FileLock("/path/to/lockfile.lock"):
            # ... exclusive access to shared resource ...
    
    The lock file is created if it doesn't exist.
    """
    
    def __init__(
        self,
        path: str,
        timeout: float = 10.0,
        retry_interval: float = 0.1,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.retry_interval = retry_interval
        self._fd: Optional[int] = None
        self._is_windows = sys.platform == "win32"
    
    def acquire(self) -> bool:
        """Acquire the lock. Returns True on success, False on timeout."""
        dir_name = os.path.dirname(self.path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        
        start_time = time.monotonic()
        
        while True:
            try:
                # Open the lock file (create if doesn't exist)
                self._fd = os.open(
                    self.path,
                    os.O_RDWR | os.O_CREAT,
                    0o644,
                )
                
                if self._is_windows:
                    self._lock_windows()
                else:
                    self._lock_unix()
                
                return True
                
            except (OSError, IOError) as e:
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None
                
                elapsed = time.monotonic() - start_time
                if elapsed >= self.timeout:
                    logger.warning("Failed to acquire lock %s: timeout after %.1fs", self.path, elapsed)
                    return False
                
                time.sleep(self.retry_interval)
    
    def release(self) -> None:
        """Release the lock."""
        if self._fd is None:
            return
        
        try:
            if self._is_windows:
                self._unlock_windows()
            else:
                self._unlock_unix()
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
    
    def _lock_unix(self) -> None:
        """Lock using fcntl on Unix."""
        import fcntl
        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    
    def _unlock_unix(self) -> None:
        """Unlock using fcntl on Unix."""
        import fcntl
        fcntl.flock(self._fd, fcntl.LOCK_UN)
    
    def _lock_windows(self) -> None:
        """Lock using msvcrt on Windows."""
        import msvcrt
        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
    
    def _unlock_windows(self) -> None:
        """Unlock using msvcrt on Windows."""
        import msvcrt
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # May fail if file was truncated
    
    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise FileLockError(f"Failed to acquire lock: {self.path}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


@contextmanager
def file_lock(path: str, timeout: float = 10.0) -> Generator[None, None, None]:
    """Context manager for file-based locking.
    
    Args:
        path: Path to the lock file (will be created if doesn't exist)
        timeout: Maximum seconds to wait for the lock
    
    Raises:
        FileLockError: If the lock cannot be acquired within the timeout
    
    Example:
        with file_lock("/path/to/data.json.lock"):
            data = load_json("/path/to/data.json")
            data["key"] = "value"
            save_json("/path/to/data.json", data)
    """
    lock = FileLock(path, timeout=timeout)
    if not lock.acquire():
        raise FileLockError(f"Failed to acquire lock: {path}")
    try:
        yield
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Atomic JSON persistence
# ---------------------------------------------------------------------------


def atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """Write JSON atomically: tmp -> flush -> fsync -> rename.

    This prevents data corruption if the process crashes mid-write.
    Used by owner.py, pairing.py, and any module that needs safe JSON persistence.
    """
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
