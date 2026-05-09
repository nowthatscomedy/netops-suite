from __future__ import annotations

import json
import logging
import subprocess

from app.models.result_models import CommandResult


class PowerShellService:
    ENCODING_PREAMBLE = (
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8;"
    )

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    @staticmethod
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def run(self, script: str, timeout: int = 20) -> CommandResult:
        effective_script = f"{self.ENCODING_PREAMBLE}\n{script}"
        self.logger.info("PowerShell start: %s", script.splitlines()[0][:120] if script else "<empty>")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    effective_script,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            self.logger.error("PowerShell timeout after %ss", timeout)
            return CommandResult(
                command=effective_script,
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"PowerShell timed out after {timeout} seconds.",
                returncode=-1,
                timed_out=True,
            )

        result = CommandResult(
            command=effective_script,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            timed_out=False,
        )
        if result.success:
            self.logger.info("PowerShell success")
        else:
            self.logger.error("PowerShell failed: rc=%s stderr=%s", result.returncode, result.stderr.strip())
        return result

    def run_json(self, script: str, timeout: int = 20) -> list | dict | None:
        result = self.run(script, timeout=timeout)
        if not result.success:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "PowerShell command failed.")
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to parse PowerShell JSON output.") from exc
