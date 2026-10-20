"""Replace the program files of an unzipped (portable) copy in place.

The running app cannot overwrite its own executable and ``_internal`` folder,
so it extracts the verified release zip to a temporary folder and starts the
new executable from there with ``--apply-portable-update``. That process waits
for the old app to exit, swaps the program files next to the user's ``data``
folder, restarts the app and records the outcome in a result file the app
reads on its next start.

This module is imported before Qt and before the data folder is resolved, so
it must only use the standard library.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

APPLY_FLAG = "--apply-portable-update"
EXECUTABLE_NAME = "NetOpsSuite.exe"
BUNDLE_DIRNAME = "_internal"
PROTECTED_NAMES = frozenset({"data"})
BACKUP_PREFIX = ".update-backup-"
RESULT_FILENAME = "portable_update_result.json"
WAIT_FOR_EXIT_SEC = 60
LOCK_RETRY_SEC = 20


class PortableUpdateError(Exception):
    pass


def find_payload_root(extracted_dir: Path, executable_name: str = EXECUTABLE_NAME) -> Path:
    """Return the folder in an extracted release zip that holds the program."""

    extracted_dir = Path(extracted_dir)
    candidates = [extracted_dir]
    children = [child for child in extracted_dir.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        candidates.append(children[0])
    for candidate in candidates:
        if (candidate / executable_name).is_file():
            validate_payload(candidate, executable_name)
            return candidate
    raise PortableUpdateError(f"압축 파일에서 {executable_name}을 찾지 못했습니다.")


def validate_payload(payload_dir: Path, executable_name: str = EXECUTABLE_NAME) -> None:
    payload_dir = Path(payload_dir)
    if not (payload_dir / executable_name).is_file():
        raise PortableUpdateError(f"새 버전 폴더에 {executable_name}이 없습니다.")
    if not (payload_dir / BUNDLE_DIRNAME).is_dir():
        raise PortableUpdateError(f"새 버전 폴더에 {BUNDLE_DIRNAME} 폴더가 없습니다.")
    for name in PROTECTED_NAMES:
        if (payload_dir / name).exists():
            raise PortableUpdateError(f"새 버전 압축 파일에 사용자 {name} 폴더가 들어 있어 적용하지 않습니다.")


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise PortableUpdateError(f"압축 파일에 허용되지 않는 경로가 있습니다: {member.filename}")
        archive.extractall(destination)


def _retry_on_lock(action, description: str, timeout_sec: float) -> None:
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            action()
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise PortableUpdateError(
                    f"{description} 파일이 사용 중이라 교체하지 못했습니다. NetOps Suite 창이 모두 닫혔는지 확인하세요."
                ) from exc
            time.sleep(0.5)


def _copy_item(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def _remove_item(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def apply_portable_update(
    payload_dir: Path,
    target_dir: Path,
    *,
    executable_name: str = EXECUTABLE_NAME,
    lock_retry_sec: float = LOCK_RETRY_SEC,
) -> None:
    """Swap the program files in ``target_dir`` for those in ``payload_dir``.

    The user's ``data`` folder and any file the release does not ship are left
    alone. Replaced items are moved into a backup folder first and moved back
    if any copy fails, so a failed update leaves the previous version working.
    """

    payload_dir = Path(payload_dir)
    target_dir = Path(target_dir)
    validate_payload(payload_dir, executable_name)
    if not (target_dir / executable_name).is_file():
        raise PortableUpdateError(f"교체할 폴더에 {executable_name}이 없습니다: {target_dir}")
    if payload_dir.resolve() == target_dir.resolve():
        raise PortableUpdateError("새 버전 폴더와 교체할 폴더가 같습니다.")

    items = sorted(
        (item for item in payload_dir.iterdir() if item.name.casefold() not in PROTECTED_NAMES),
        key=lambda item: item.name,
    )
    backup_dir = target_dir / f"{BACKUP_PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backup_dir.mkdir()
    moved: list[str] = []
    copied: list[str] = []
    try:
        for item in items:
            current = target_dir / item.name
            if current.exists() or current.is_symlink():
                _retry_on_lock(
                    lambda current=current: os.replace(current, backup_dir / current.name),
                    item.name,
                    lock_retry_sec,
                )
                moved.append(item.name)
        for item in items:
            _copy_item(item, target_dir / item.name)
            copied.append(item.name)
    except Exception as exc:
        rollback_errors: list[str] = []
        for name in copied:
            try:
                _remove_item(target_dir / name)
            except OSError as rollback_exc:
                rollback_errors.append(f"{name}: {rollback_exc}")
        for name in moved:
            try:
                if (target_dir / name).exists():
                    _remove_item(target_dir / name)
                os.replace(backup_dir / name, target_dir / name)
            except OSError as rollback_exc:
                rollback_errors.append(f"{name}: {rollback_exc}")
        if not rollback_errors:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if isinstance(exc, PortableUpdateError):
                raise
            raise PortableUpdateError(f"새 파일을 복사하지 못해 이전 버전으로 되돌렸습니다: {exc}") from exc
        raise PortableUpdateError(
            "새 파일 복사와 이전 버전 복구에 모두 실패했습니다. "
            f"{backup_dir.name} 폴더에 이전 파일이 남아 있습니다. ({'; '.join(rollback_errors)})"
        ) from exc

    shutil.rmtree(backup_dir, ignore_errors=True)


def write_result(result_file: Path | None, *, success: bool, version: str, message: str) -> None:
    if result_file is None:
        return
    try:
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            json.dumps(
                {
                    "success": success,
                    "version": version,
                    "message": message,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def read_and_clear_result(result_file: Path) -> dict | None:
    """Return the outcome of the last portable update once, then forget it."""

    try:
        payload = json.loads(Path(result_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        Path(result_file).unlink()
    except OSError:
        pass
    return payload if isinstance(payload, dict) else None


def wait_for_process_exit(pid: int, timeout_sec: float = WAIT_FOR_EXIT_SEC) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        synchronize = 0x00100000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        try:
            kernel32.WaitForSingleObject(handle, int(timeout_sec * 1000))
        finally:
            kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)


def launch_apply_process(
    payload_dir: Path,
    target_dir: Path,
    *,
    wait_pid: int,
    version: str,
    result_file: Path,
    executable_name: str = EXECUTABLE_NAME,
) -> None:
    """Start the new executable from its temporary folder to swap the files."""

    new_executable = Path(payload_dir) / executable_name
    flags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, flag_name, 0)
    subprocess.Popen(
        [
            str(new_executable),
            APPLY_FLAG,
            "--target",
            str(target_dir),
            "--wait-pid",
            str(wait_pid),
            "--version",
            version,
            "--result-file",
            str(result_file),
        ],
        cwd=str(Path(payload_dir).parent),
        creationflags=flags,
        close_fds=True,
    )


def _relaunch(target_dir: Path, executable_name: str) -> None:
    executable = target_dir / executable_name
    if not executable.is_file():
        return
    flags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, flag_name, 0)
    subprocess.Popen([str(executable)], cwd=str(target_dir), creationflags=flags, close_fds=True)


def run_apply_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=APPLY_FLAG, add_help=False)
    parser.add_argument("--target", required=True)
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--version", default="")
    parser.add_argument("--result-file", default="")
    parser.add_argument("--no-relaunch", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    target_dir = Path(args.target)
    result_file = Path(args.result_file) if args.result_file else None
    payload_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    exit_code = 0
    try:
        if payload_dir is None:
            raise PortableUpdateError("포터블 업데이트는 배포된 실행 파일에서만 적용할 수 있습니다.")
        wait_for_process_exit(args.wait_pid)
        apply_portable_update(payload_dir, target_dir)
        write_result(
            result_file,
            success=True,
            version=args.version,
            message=f"포터블 폴더를 {args.version} 버전으로 교체했습니다.",
        )
    except Exception as exc:
        exit_code = 1
        write_result(result_file, success=False, version=args.version, message=str(exc))
    if not args.no_relaunch:
        try:
            _relaunch(target_dir, EXECUTABLE_NAME)
        except OSError:
            pass
    return exit_code
