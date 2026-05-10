from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lvnotes.core.exceptions import CacheError


@contextmanager
def file_lock(lock_path: Path, blocking: bool = True) -> Iterator[Path]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError as exc:
        raise CacheError("file locking requires POSIX fcntl support") from exc

    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise CacheError(f"failed to acquire lock {lock_path}: {exc}") from exc
    with handle:
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as exc:
            raise CacheError(f"lock is already held: {lock_path}") from exc
        except OSError as exc:
            raise CacheError(f"failed to acquire lock {lock_path}: {exc}") from exc
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def input_cache_lock_path(run_dir: Path) -> Path:
    return run_dir / ".lvnotes.lock"


def trim_lock_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}.lock")


@contextmanager
def input_cache_lock(run_dir: Path, blocking: bool = True) -> Iterator[Path]:
    with file_lock(input_cache_lock_path(run_dir), blocking) as lock_path:
        yield lock_path


@contextmanager
def trim_output_lock(output_path: Path, blocking: bool = True) -> Iterator[Path]:
    with file_lock(trim_lock_path(output_path), blocking) as lock_path:
        yield lock_path
