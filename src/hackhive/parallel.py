"""One function over many items in a pool of processes, results in input order (#22's zone
ranking; #103's plan loops are meant to use it too)."""

from __future__ import annotations

import multiprocessing
import os
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

MAX_WORKERS = 4  # #103's benchmark on Adi's M5: 4 workers give the whole gain, 6 or 10 add nothing


def default_workers() -> int:
    return min(MAX_WORKERS, os.cpu_count() or 1)


def map_in_order(fn: Callable[[T], R], items: Iterable[T], workers: int | None = None) -> list[R]:
    """`fn` over `items`, results in input order. `workers=None` means `default_workers()`; 1 (or
    a single item) runs inline with no pool, for pytest and debugging.

    `fn` must pickle: a module-level function of the `hackhive` package, or a `functools.partial`
    of one with picklable arguments. macOS and Windows start workers with spawn, which imports
    `fn` by name, and a monkeypatch in the parent doesn't reach them.
    """
    items = list(items)
    n = min(workers or default_workers(), len(items))
    if n <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(n, mp_context=_context()) as pool:
        return list(pool.map(fn, items))


def _context() -> multiprocessing.context.BaseContext | None:
    """Workers start from a fork server, never by forking the caller (#26): Streamlit runs the app
    on threads, and forking a threaded process can deadlock. Scoped to these pools, so it never
    changes the process-wide start method. Windows always spawns (None: its default)."""
    return None if sys.platform == "win32" else multiprocessing.get_context("forkserver")
