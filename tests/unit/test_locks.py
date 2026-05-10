from pathlib import Path

import pytest

from lvnotes.core.exceptions import CacheError
from lvnotes.core.locks import file_lock, input_cache_lock, input_cache_lock_path, trim_lock_path


def test_input_cache_lock_path_uses_run_dir_lock_file(tmp_path: Path) -> None:
    assert input_cache_lock_path(tmp_path / "cache" / "abc") == tmp_path / "cache" / "abc" / ".lvnotes.lock"


def test_trim_lock_path_is_hidden_sibling(tmp_path: Path) -> None:
    output = tmp_path / "lecture.head-10m.mp4"

    assert trim_lock_path(output) == tmp_path / ".lecture.head-10m.mp4.lock"


def test_file_lock_rejects_second_nonblocking_holder(tmp_path: Path) -> None:
    lock_path = tmp_path / "locks" / "test.lock"

    with file_lock(lock_path):
        with pytest.raises(CacheError, match="already held"):
            with file_lock(lock_path, blocking=False):
                pass


def test_input_cache_lock_creates_run_dir_and_lock_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "cache" / "inputhash"

    with input_cache_lock(run_dir) as lock_path:
        assert lock_path == run_dir / ".lvnotes.lock"
        assert lock_path.exists()


def test_file_lock_does_not_wrap_body_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="body failed"):
        with file_lock(tmp_path / "test.lock"):
            raise OSError("body failed")
