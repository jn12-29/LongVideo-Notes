import pytest

from lvnotes.core.parallel import run_parallel


def test_run_parallel_preserves_input_order() -> None:
    results = run_parallel([3, 1, 2], lambda value: value * 10, desc="test.parallel", unit="item", max_workers=3)

    assert results == [30, 10, 20]


def test_run_parallel_supports_serial_execution() -> None:
    calls: list[int] = []

    def worker(value: int) -> int:
        calls.append(value)
        return value + 1

    results = run_parallel([1, 2], worker, desc="test.serial", unit="item", max_workers=1)

    assert calls == [1, 2]
    assert results == [2, 3]


def test_run_parallel_propagates_worker_errors() -> None:
    def worker(value: int) -> int:
        if value == 2:
            raise RuntimeError("boom")
        return value

    with pytest.raises(RuntimeError, match="boom"):
        run_parallel([1, 2, 3], worker, desc="test.error", unit="item", max_workers=2)


def test_run_parallel_rejects_non_positive_workers() -> None:
    with pytest.raises(ValueError, match="max_workers"):
        run_parallel([1], lambda value: value, desc="test.invalid", unit="item", max_workers=0)
