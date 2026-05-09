# NetOps Suite Local Integration

This folder is the local-first unified GUI build.

## Shape

- `app/`: existing NetOps Toolkit PySide6 GUI kept as the compatibility shell.
- `netops_suite/core/`: shared event/model foundation for new integrations.
- `netops_suite/modules/inspector_runtime/`: migrated NetOps Inspector runtime (`core`, `vendors`, `locales`) preserved for compatibility.
- `netops_suite/modules/inspector/`: GUI-friendly `InspectorService` wrapper.
- `netops_suite/modules/config_builder/`: migrated Switch Config Builder runtime and `ConfigBuilderService`.
- `app/ui/tabs/inspector_tab.py`: unified `장비 점검` tab.
- `app/ui/tabs/config_builder_tab.py`: unified `설정 생성` tab.

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

The main window title is `NetOps Suite`; runtime data uses `NetOps Suite` under `%LOCALAPPDATA%` when installed in a protected path.

## Verify

```powershell
python -m compileall -q main.py app netops_suite tests
python -m pytest -q
```

Current verification result:

- `48 passed`
- Telnet compatibility uses `telnetlib3`; no stdlib `telnetlib` deprecation warning is expected.

## Release Signing

Windows installer code signing is optional. The release workflow can publish an
unsigned installer, and local builds can require Authenticode signing by passing
`-RequireCodeSigning` with a certificate path/password.

## Notes

- The `전송/성능` functions from the plan are preserved inside the existing `진단` tab as `파일 전송` and `iperf3` sub-tabs.
- NetOps Inspector CLI/TUI entry points were not exposed in the unified app. The backend engine is available through `InspectorService`.
- The full Switch Config Builder desktop editor can be opened from the `설정 생성` tab while the common render path is available directly in the tab.

