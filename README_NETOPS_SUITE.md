# NetOps Suite Local Integration

This repository contains the local-first unified PySide6 GUI build.

## Shape

- `app/`: the main NetOps Suite desktop shell.
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

The main window title is `NetOps Suite`. Runtime data uses `%LOCALAPPDATA%\NetOps Suite` when the installed app runs from a protected path such as `Program Files`.

## Verify

```powershell
python -m compileall -q main.py app netops_suite tests
python -m pytest -q
python -m pip check
```

Current local verification result: `75 passed`.

## Release Safety

- GitHub Actions release builds are manual `workflow_dispatch` runs.
- Existing release assets are not replaced unless `allow_asset_replace` is explicitly enabled.
- Installer downloads are SHA-256 checked for file integrity. Publisher trust is separate and should be verified with Windows code signing.
- Before public release, run `gitleaks detect --source . --verbose --redact` and block release if history contains secrets.

## Release Signing

Windows installer code signing is optional. Local builds can require Authenticode signing by passing `-RequireCodeSigning` with a certificate path/password. Unsigned installers should be treated as lower-trust artifacts by users.

## Notes

- File transfer and performance tools live in the existing `진단` tab as `파일 전송` and `iperf3` sub-tabs.
- NetOps Inspector CLI/TUI entry points are not exposed in the unified app. The backend engine is available through `InspectorService`.
- The full Switch Config Builder desktop editor opens from the `설정 생성` tab, while the common render path is available directly in the tab.
