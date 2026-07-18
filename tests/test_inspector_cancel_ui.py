from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.ui.tabs.inspector_tab import InspectorTab
from netops_suite.modules.inspector import InspectorRunResult


class ControlledThreadPool:
    def __init__(self) -> None:
        self.pending: list[object] = []

    def start(self, worker) -> None:
        self.pending.append(worker)

    def release_next(self) -> None:
        assert self.pending
        self.pending.pop(0).run()


class CancelAwareInspectorService:
    def __init__(self) -> None:
        self.cancel_events: list[object] = []

    def run(
        self,
        _request,
        *,
        progress_callback=None,
        cancel_event=None,
    ):
        del progress_callback
        self.cancel_events.append(cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("장비 점검 작업이 취소되었습니다.")
        raise AssertionError("테스트에서는 중지 요청 후에만 작업을 완료해야 합니다.")


class FinishingInspectorService:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome

    def run(
        self,
        request,
        *,
        progress_callback=None,
        cancel_event=None,
    ):
        del progress_callback, cancel_event
        if self.outcome == "error":
            raise RuntimeError("장비 연결 실패")
        return InspectorRunResult(
            mode=request.mode,
            devices_total=1,
            results_total=1,
            result_excel=None,
            backup_dir=None,
            session_log_dir=None,
            results=[],
        )


def build_inspector_tab(tmp_path, service):
    thread_pool = ControlledThreadPool()
    state = SimpleNamespace(
        thread_pool=thread_pool,
        paths=SimpleNamespace(data_root=tmp_path / "data"),
    )
    tab = InspectorTab(state)
    tab.service = service
    tab.inventory_path_edit.setText(str(tmp_path / "inventory.xlsx"))
    return tab, thread_pool


def test_inspector_stop_button_physically_requests_cancel_and_restores_actions(
    qapp,
    tmp_path,
    monkeypatch,
):
    service = CancelAwareInspectorService()
    tab, thread_pool = build_inspector_tab(tmp_path, service)
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.inspector_tab.confirm_risky_action",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.ui.tabs.inspector_tab.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    try:
        tab.resize(1100, 760)
        tab.show()
        qapp.processEvents()

        assert tab.cancel_button.isVisibleTo(tab)
        assert tab.cancel_button.text() == "중지"
        assert tab.cancel_button.objectName() == "inspectorCancelButton"
        assert not tab.cancel_button.isEnabled()
        assert tab.run_button.isEnabled()

        QTest.mouseClick(tab.run_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert tab._inspector_running
        assert len(thread_pool.pending) == 1
        assert not tab.run_button.isEnabled()
        assert tab.cancel_button.isEnabled()

        QTest.mouseClick(tab.cancel_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        cancel_event = tab._cancel_event
        assert cancel_event is not None
        assert cancel_event.is_set()
        assert not tab.run_button.isEnabled()
        assert not tab.cancel_button.isEnabled()
        assert "중지 요청" in tab.validation_status_label.text()
        assert "[cancel]" in tab.log_view.toPlainText()

        QTest.mouseClick(tab.run_button, Qt.MouseButton.LeftButton)
        tab._run_inspector()
        assert len(thread_pool.pending) == 1
        assert tab.log_view.toPlainText().count("[cancel]") == 1

        thread_pool.release_next()
        qapp.processEvents()

        assert service.cancel_events == [cancel_event]
        assert warnings == []
        assert not tab._inspector_running
        assert tab._cancel_event is None
        assert tab.run_button.isEnabled()
        assert not tab.cancel_button.isEnabled()
        assert "중지되었습니다" in tab.summary_label.text()
        assert "[cancelled]" in tab.log_view.toPlainText()
    finally:
        tab.close()


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_inspector_run_buttons_restore_after_worker_finishes(
    qapp,
    tmp_path,
    monkeypatch,
    outcome,
):
    tab, thread_pool = build_inspector_tab(
        tmp_path, FinishingInspectorService(outcome)
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.inspector_tab.confirm_risky_action",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.ui.tabs.inspector_tab.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    try:
        tab.show()
        qapp.processEvents()

        QTest.mouseClick(tab.run_button, Qt.MouseButton.LeftButton)
        assert not tab.run_button.isEnabled()
        assert tab.cancel_button.isEnabled()
        assert len(thread_pool.pending) == 1

        thread_pool.release_next()
        qapp.processEvents()

        assert not tab._inspector_running
        assert tab._cancel_event is None
        assert tab.run_button.isEnabled()
        assert not tab.cancel_button.isEnabled()
        if outcome == "success":
            assert warnings == []
            assert tab.summary_label.text().startswith("완료:")
        else:
            assert warnings == ["장비 연결 실패"]
            assert tab.summary_label.text() == "장비 점검 실패"
    finally:
        tab.close()
