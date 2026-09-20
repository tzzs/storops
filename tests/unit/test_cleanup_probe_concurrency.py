"""Unit tests for core/cleanup.py's _sized_probes().

Every probe path in a cleanup plan is sized by walking its whole subtree,
so a plan is almost entirely time spent waiting on filesystem metadata --
measured at 6.3s for nine probes on a real Mac, down to 2.8s once they run
concurrently. Concurrency is opt-in per backend because the Windows native
backend keeps per-call state on `self` and already parallelizes its own
walk internally.
"""
from __future__ import annotations

import threading

from storops.core.cleanup import _sized_probes
from storops.core.models import Entry


class _Backend:
    def __init__(self, *, concurrent: bool):
        if concurrent:
            self.path_size_is_concurrent = True
        self.calls: list[str] = []
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self._gate = threading.Barrier(2, timeout=5)

    def path_size(self, path, *, admin=False):
        with self._lock:
            self.calls.append(path)
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            # Blocks until a second call joins -- which can only ever
            # happen if the caller really is running them concurrently.
            try:
                self._gate.wait()
            except threading.BrokenBarrierError:
                pass
        finally:
            with self._lock:
                self._in_flight -= 1
        return Entry(full_name=path, is_folder=True, size_bytes=1, allocated_bytes=1)


def test_probes_are_sized_concurrently_when_the_backend_allows_it():
    backend = _Backend(concurrent=True)

    results = _sized_probes(backend, ["/a", "/b"], admin=False)

    assert [r.full_name for r in results] == ["/a", "/b"]  # order preserved
    assert backend.max_in_flight == 2


def test_probes_stay_sequential_for_a_backend_that_does_not_opt_in():
    backend = _Backend(concurrent=False)
    backend._gate.abort()  # a sequential caller would otherwise deadlock

    results = _sized_probes(backend, ["/a", "/b"], admin=False)

    assert [r.full_name for r in results] == ["/a", "/b"]
    assert backend.max_in_flight == 1


def test_a_single_probe_never_spins_up_a_pool():
    backend = _Backend(concurrent=True)
    backend._gate.abort()

    results = _sized_probes(backend, ["/only"], admin=False)

    assert [r.full_name for r in results] == ["/only"]
    assert backend.max_in_flight == 1
