from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QMessageBox

from app.utils.threading_utils import FunctionWorker


class JobRunner:
    def __init__(
        self,
        thread_pool: Any,
        parent: Any = None,
        default_error_title: str = "작업 실패",
    ) -> None:
        self.thread_pool = thread_pool
        self.parent = parent
        self.default_error_title = default_error_title
        self._active_workers: list[FunctionWorker] = []

    def start(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_started: Callable[[], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
        on_result: Callable[[object], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        error_title: str | None = None,
        **kwargs: Any,
    ) -> None:
        worker = FunctionWorker(fn, *args, **kwargs)
        self._active_workers.append(worker)

        if on_started:
            worker.signals.started.connect(on_started)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_finished:
            worker.signals.finished.connect(on_finished)

        if on_error:
            worker.signals.error.connect(on_error)
        else:
            title = error_title or self.default_error_title
            worker.signals.error.connect(lambda text, title=title: QMessageBox.warning(self.parent, title, text))

        worker.signals.finished.connect(lambda worker=worker: self._discard_worker(worker))
        self.thread_pool.start(worker)

    def _discard_worker(self, worker: FunctionWorker) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)
