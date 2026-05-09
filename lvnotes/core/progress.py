from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import sys
from threading import Lock
from typing import TypeVar

from tqdm import tqdm

ProgressT = TypeVar("ProgressT")

_OUTPUT_LOCK = Lock()


def progress_enabled() -> bool:
    return sys.stderr.isatty()


def progress_write(message: str) -> None:
    with _OUTPUT_LOCK:
        tqdm.write(message)


@contextmanager
def progress_bar(*, desc: str, total: float | int, unit: str) -> Iterator[tqdm]:
    bar = tqdm(
        total=total,
        desc=desc,
        unit=unit,
        dynamic_ncols=True,
        disable=not progress_enabled(),
    )
    try:
        yield bar
    finally:
        bar.close()


def progress_iter(
    iterable: Iterable[ProgressT],
    *,
    desc: str,
    total: int | None = None,
    unit: str = "item",
) -> Iterator[ProgressT]:
    yield from tqdm(
        iterable,
        desc=desc,
        total=total,
        unit=unit,
        dynamic_ncols=True,
        disable=not progress_enabled(),
    )
