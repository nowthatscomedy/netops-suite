from __future__ import annotations

import inspect
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(object)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            self.signals.started.emit()
        except RuntimeError:
            return
        try:
            parameters = inspect.signature(self.fn).parameters
            if "progress_callback" in parameters and "progress_callback" not in self.kwargs:
                self.kwargs["progress_callback"] = self.signals.progress
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                return
        else:
            try:
                self.signals.result.emit(result)
            except RuntimeError:
                return
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass
