from __future__ import annotations

from types import SimpleNamespace

import main as main_module


class _FakeApplication:
    def __init__(self, _argv):
        self.aboutToQuit = SimpleNamespace(connect=lambda _callback: None)

    def setApplicationName(self, _value):
        pass

    def setOrganizationName(self, _value):
        pass

    def setApplicationVersion(self, _value):
        pass

    def setWindowIcon(self, _value):
        pass

    def exec(self):
        return 0


class _FakeLoadingWindow:
    def __init__(self, _version):
        pass

    def setWindowIcon(self, _value):
        pass

    def show(self):
        pass

    def set_step(self, *_args):
        pass

    def fail(self, *_args):
        pass


class _FakeIcon:
    def isNull(self):
        return True


def _stub_startup_ui(monkeypatch):
    monkeypatch.setattr(main_module, "_set_windows_app_id", lambda: None)
    monkeypatch.setattr(main_module, "QApplication", _FakeApplication)
    monkeypatch.setattr(main_module, "StartupLoadingWindow", _FakeLoadingWindow)
    monkeypatch.setattr(main_module, "apply_app_theme", lambda _app: None)
    monkeypatch.setattr(main_module, "load_app_icon", _FakeIcon)
    monkeypatch.setattr(main_module.QMessageBox, "critical", lambda *_args: None)


def test_main_version_flag_exits_without_starting_ui(monkeypatch, capsys):
    monkeypatch.setattr(main_module.sys, "argv", ["NetOpsSuite.exe", "--version"])
    monkeypatch.setattr(main_module, "QApplication", lambda _argv: (_ for _ in ()).throw(AssertionError("UI started")))

    assert main_module.main() == 0
    assert main_module.__version__ in capsys.readouterr().out


def test_main_release_smoke_flag_dispatches_before_normal_ui(monkeypatch):
    monkeypatch.setattr(main_module.sys, "argv", ["NetOpsSuite.exe", "--release-smoke-test"])
    monkeypatch.setattr(main_module, "_run_release_smoke_test", lambda: 23)
    monkeypatch.setattr(
        main_module,
        "QApplication",
        lambda _argv: (_ for _ in ()).throw(AssertionError("normal UI started")),
    )

    assert main_module.main() == 23


def test_main_cleans_up_partial_state_when_window_creation_fails(monkeypatch):
    _stub_startup_ui(monkeypatch)
    monkeypatch.setattr(main_module.sys, "argv", ["NetOpsSuite.exe"])
    state = SimpleNamespace(shutdown_calls=0)

    def shutdown():
        state.shutdown_calls += 1

    state.shutdown = shutdown
    monkeypatch.setattr(main_module, "AppState", lambda **_kwargs: state)
    monkeypatch.setattr(
        main_module,
        "MainWindow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("window failed")),
    )

    assert main_module.main() == 1
    assert state.shutdown_calls == 1


def test_main_handles_state_constructor_failure_without_unbound_cleanup(monkeypatch):
    _stub_startup_ui(monkeypatch)
    monkeypatch.setattr(main_module.sys, "argv", ["NetOpsSuite.exe"])
    monkeypatch.setattr(
        main_module,
        "AppState",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("state failed")),
    )

    assert main_module.main() == 1
