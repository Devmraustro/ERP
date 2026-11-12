# SPDX-License-Identifier: LGPL-3.0-only
"""Off-the-GUI-thread API workers (Phase 29).

Every screen loads its data through :class:`ApiWorker`. The rules that keep
the window usable and the failures visible:

* one HTTP round trip (or one small batch) per worker, run on a ``QThread``;
* results and errors come back as queued signals on the GUI thread;
* every exception is normalised to :class:`~dz_erp.desktop.api_client.ErpApiError`
  so a screen has exactly one error path to render;
* the owner keeps a reference for the worker's lifetime (a ``QThread`` that is
  garbage-collected while running aborts the process).
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from PySide6.QtCore import QThread, Signal

from .api_client import ErpApiError


class ApiWorker(QThread):
    """Run one client call off the GUI thread; report via signals."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent: Any = None, **kwargs: Any) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # executed on the worker thread
        try:
            result = self._fn(*self._args, **self._kwargs)
        except ErpApiError as exc:
            self.failed.emit(exc)
        except Exception as exc:  # the UI surfaces, never crashes
            self.failed.emit(ErpApiError(0, str(exc)))
        else:
            self.succeeded.emit(result)


class WorkerOwner:
    """Mixin that keeps workers alive and offers a one-call ``run`` helper.

    ``_workers`` is inspected by the existing test suites (they wait until
    every worker has finished), so the attribute is part of the contract and
    is pruned only of *finished* workers.
    """

    def _init_workers(self) -> None:
        self._workers: List[ApiWorker] = []

    def _track(self, worker: ApiWorker) -> ApiWorker:
        self._workers = [existing for existing in self._workers if not existing.isFinished()]
        self._workers.append(worker)
        return worker

    def run_async(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Any], None]] = None,
        **kwargs: Any,
    ) -> ApiWorker:
        worker = self._track(ApiWorker(fn, *args, parent=self, **kwargs))
        if on_success is not None:
            worker.succeeded.connect(on_success)
        if on_error is not None:
            worker.failed.connect(on_error)
        worker.start()
        return worker


__all__ = ["ApiWorker", "WorkerOwner"]
