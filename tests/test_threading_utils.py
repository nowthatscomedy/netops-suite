from __future__ import annotations

from app.ui.common.job_runner import JobRunner
from app.utils.threading_utils import FunctionWorker


def test_job_runner_start_failure_reports_error_finishes_and_releases_worker(
    qapp,
    caplog,
):
    class FailingThreadPool:
        def start(self, _worker):
            raise RuntimeError("thread pool is unavailable")

    errors: list[str] = []
    finished: list[bool] = []
    runner = JobRunner(FailingThreadPool())

    runner.start(
        lambda: "unused",
        on_error=errors.append,
        on_finished=lambda: finished.append(True),
    )

    assert errors == ["thread pool is unavailable"]
    assert finished == [True]
    assert runner._active_workers == []
    assert "Failed to submit background job" in caplog.text
    assert "RuntimeError: thread pool is unavailable" in caplog.text


def test_function_worker_emits_one_terminal_callback_and_always_finishes(qapp):
    result_values: list[object] = []
    result_errors: list[str] = []
    result_finished: list[bool] = []
    result_worker = FunctionWorker(lambda: "done")
    result_worker.signals.result.connect(result_values.append)
    result_worker.signals.error.connect(result_errors.append)
    result_worker.signals.finished.connect(lambda: result_finished.append(True))

    result_worker.run()

    assert result_values == ["done"]
    assert result_errors == []
    assert result_finished == [True]

    error_values: list[object] = []
    error_messages: list[str] = []
    error_finished: list[bool] = []

    def fail():
        raise ValueError("worker failed")

    error_worker = FunctionWorker(fail)
    error_worker.signals.result.connect(error_values.append)
    error_worker.signals.error.connect(error_messages.append)
    error_worker.signals.finished.connect(lambda: error_finished.append(True))

    error_worker.run()

    assert error_values == []
    assert error_messages == ["worker failed"]
    assert error_finished == [True]
