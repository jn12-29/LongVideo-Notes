from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TypeVar

from lvnotes.core.progress import progress_bar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True)
class _ResultSlot:
    value: object


def run_parallel(
    items: Sequence[InputT],
    worker: Callable[[InputT], OutputT],
    *,
    desc: str,
    unit: str,
    max_workers: int,
) -> list[OutputT]:
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    if not items:
        return []
    if max_workers == 1:
        results: list[OutputT] = []
        with progress_bar(desc=desc, total=len(items), unit=unit) as bar:
            for item in items:
                results.append(worker(item))
                bar.update(1)
        return results

    results: list[_ResultSlot | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, item): index for index, item in enumerate(items)}
        with progress_bar(desc=desc, total=len(futures), unit=unit) as bar:
            for future in as_completed(futures):
                results[futures[future]] = _ResultSlot(future.result())
                bar.update(1)
    return [slot.value for slot in results if slot is not None]  # type: ignore[return-value]
