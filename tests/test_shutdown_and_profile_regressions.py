from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from app.main_window import MainWindow
from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from app.ui.tabs.diagnostics_tab import DiagnosticsTab
from app.ui.tabs.wireless_tab import WirelessTab
from netops_suite.modules.config_builder.switch_configurator.desktop_impl import SwitchConfigBuilderWidget
from netops_suite.modules.inspector import InspectorRunRequest, InspectorService


class _CountingEvent:
    def __init__(self) -> None:
        self.set_calls = 0

    def set(self) -> None:
        self.set_calls += 1


class _CountingTimer:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class _Checked:
    def __init__(self, checked: bool) -> None:
        self.checked = checked

    def isChecked(self) -> bool:
        return self.checked


def test_diagnostics_shutdown_cancels_every_job_once() -> None:
    event_names = (
        "ftp_server_cancel_event",
        "scp_server_cancel_event",
        "tftp_server_cancel_event",
        "ftp_client_cancel_event",
        "scp_client_cancel_event",
        "tftp_client_cancel_event",
        "ping_cancel_event",
        "tcp_cancel_event",
        "trace_cancel_event",
        "arp_cancel_event",
        "iperf_cancel_event",
        "iperf_manage_cancel_event",
    )
    events = {name: _CountingEvent() for name in event_names}
    subject = SimpleNamespace(_shutting_down=False, **events)

    DiagnosticsTab.shutdown(subject)
    DiagnosticsTab.shutdown(subject)

    assert subject._shutting_down is True
    assert all(event.set_calls == 1 for event in events.values())


def test_diagnostics_finished_ftp_job_does_not_refresh_during_shutdown() -> None:
    calls: list[object] = []
    subject = SimpleNamespace(
        _shutting_down=True,
        _ftp_client_connected=True,
        _ftp_session_id="session-id",
        _set_ftp_client_busy=lambda busy: calls.append(("busy", busy)),
        _refresh_ftp_remote_list=lambda: calls.append("refresh"),
    )

    DiagnosticsTab._finish_ftp_job_with_refresh(subject)

    assert calls == [("busy", False)]


def test_wireless_shutdown_stops_all_timers_and_rejects_new_workers() -> None:
    timers = [_CountingTimer(), _CountingTimer(), _CountingTimer()]
    started_workers: list[object] = []
    subject = SimpleNamespace(
        _shutting_down=False,
        timer=timers[0],
        nearby_timer=timers[1],
        _status_grid_timer=timers[2],
        _active_workers=[],
        state=SimpleNamespace(thread_pool=SimpleNamespace(start=started_workers.append)),
    )

    WirelessTab.shutdown(subject)
    WirelessTab.shutdown(subject)
    WirelessTab._start_worker(subject, lambda: "should not run")

    assert subject._shutting_down is True
    assert [timer.stop_calls for timer in timers] == [1, 1, 1]
    assert subject._active_workers == []
    assert started_workers == []


def test_inspector_service_honors_pre_cancel_before_inventory_or_device_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    cancel_event = Event()
    cancel_event.set()

    def fail_inventory(*_args, **_kwargs):
        raise AssertionError("a pre-cancelled run must not load the inventory")

    monkeypatch.setattr(service, "load_inventory", fail_inventory)

    with pytest.raises(RuntimeError, match="취소"):
        service.run(
            InspectorRunRequest(inventory_path=str(tmp_path / "devices.xlsx")),
            cancel_event=cancel_event,
        )


def test_network_inspector_pre_cancel_does_not_dispatch_device_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    cancel_event = Event()
    cancel_event.set()
    monkeypatch.chdir(tmp_path)

    with service._runtime_import_path():
        from core.inspector import NetworkInspector

        inspector = NetworkInspector(
            "inspection_results.xlsx",
            inspection_only=True,
            cancel_event=cancel_event,
        )
        inspector.devices = [{"ip": "192.0.2.10", "vendor": "cisco", "os": "ios"}]
        calls: list[dict] = []
        monkeypatch.setattr(inspector, "_inspect_device", calls.append)

        inspector.inspect_devices()

    assert calls == []
    assert inspector.results == []


def test_vendor_profile_generation_writes_only_to_user_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = InspectorService(user_data_dir=tmp_path / "user-data")
    bundled_dir = tmp_path / "bundled-read-only"
    bundled_dir.mkdir()
    sentinel = bundled_dir / "sentinel.yaml"
    sentinel.write_text("bundled", encoding="utf-8")
    service.bundled_vendor_profiles_dir = bundled_dir
    monkeypatch.setattr(
        service,
        "supported_profile_definitions",
        lambda: [{"vendor": "Acme", "os": "NetOS"}],
    )
    monkeypatch.setattr(
        service,
        "build_vendor_profile_yaml",
        lambda vendor, os_name: f"vendor: {vendor}\nos: {os_name}\n",
    )

    count = service.ensure_vendor_profile_files()

    generated = service.vendor_profiles_dir / "acme__netos.yaml"
    assert count == 1
    assert generated.read_text(encoding="utf-8") == "vendor: Acme\nos: NetOS\n"
    assert sentinel.read_text(encoding="utf-8") == "bundled"
    assert list(bundled_dir.iterdir()) == [sentinel]


def test_user_vendor_profile_takes_precedence_over_same_bundled_key(tmp_path: Path) -> None:
    service = InspectorService(user_data_dir=tmp_path / "z-user-data")
    service.bundled_vendor_profiles_dir = tmp_path / "a-bundled"
    service.vendor_profiles_dir.mkdir(parents=True)
    service.bundled_vendor_profiles_dir.mkdir(parents=True)

    def write_profile(path: Path, command: str, display_name: str) -> None:
        path.write_text(
            "\n".join(
                [
                    "inspection_commands:",
                    "  acme:",
                    "    netos:",
                    f"      - {command}",
                    "profile_metadata:",
                    "  acme:",
                    "    netos:",
                    "      reference: true",
                    f"      display_name: {display_name}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    write_profile(service.vendor_profiles_dir / "acme.yaml", "show user", "User profile")
    write_profile(service.bundled_vendor_profiles_dir / "acme.yaml", "show bundled", "Bundled profile")

    profiles = service._load_reference_profile_files(set(), [])

    assert len(profiles) == 1
    assert profiles[0]["commands"] == ["show user"]
    assert profiles[0]["display_name"] == "User profile"
    assert Path(profiles[0]["source"]) == service.vendor_profiles_dir / "acme.yaml"


def test_config_builder_prepare_close_autosaves_once_and_stops_timers(tmp_path: Path) -> None:
    calls: list[object] = []
    subject = SimpleNamespace(
        _close_prepared=False,
        auto_save_check=_Checked(True),
        current_file_path=tmp_path / "devices.csv",
        is_dirty=True,
        _save_to_path=lambda path, *, autosave: calls.append(("save", path, autosave)) or True,
        _save_app_state=lambda: calls.append("state"),
        _stop_ui_timers=lambda: calls.append("timers"),
        _confirm_discard_changes=lambda: (_ for _ in ()).throw(
            AssertionError("successful autosave must not ask to discard changes")
        ),
    )

    assert SwitchConfigBuilderWidget.prepare_close(subject) is True
    assert SwitchConfigBuilderWidget.prepare_close(subject) is True

    assert calls == [("save", tmp_path / "devices.csv", True), "state", "timers"]
    assert subject._close_prepared is True


def test_config_builder_tab_blocks_close_when_visible_editor_refuses() -> None:
    builder_calls: list[str] = []
    editor = SimpleNamespace(isVisible=lambda: True, close=lambda: False)
    subject = SimpleNamespace(
        _builder_window=editor,
        builder_widget=SimpleNamespace(prepare_close=lambda: builder_calls.append("embedded") or True),
    )

    assert ConfigBuilderTab.prepare_close(subject) is False
    assert builder_calls == []


def test_main_window_shutdown_is_ordered_and_idempotent() -> None:
    calls: list[object] = []

    def tab(name: str):
        return SimpleNamespace(shutdown=lambda: calls.append(name))

    state = SimpleNamespace(
        thread_pool=SimpleNamespace(waitForDone=lambda timeout: calls.append(("wait", timeout))),
        shutdown=lambda: calls.append("state"),
    )
    subject = SimpleNamespace(
        _shutdown_started=False,
        diagnostics_tab=tab("diagnostics"),
        wireless_tab=tab("wireless"),
        inspector_tab=tab("inspector"),
        ai_chat_tab=tab("ai"),
        state=state,
    )

    MainWindow.shutdown(subject)
    MainWindow.shutdown(subject)

    assert calls == [
        "diagnostics",
        "wireless",
        "inspector",
        "ai",
        ("wait", 5000),
        "state",
    ]
