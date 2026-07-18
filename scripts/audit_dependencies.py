from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


# Netmiko 4.7 requires Paramiko <5 while the first upstream release fixing
# GHSA-r374-rxx8-8654 is Paramiko 5. This low-severity SHA-1 compatibility
# exception is deliberately short-lived: after the deadline CI must fail until
# the dependency is upgraded, replaced, or the exception is explicitly reviewed.
PARAMIKO_EXCEPTION_IDS = ("GHSA-r374-rxx8-8654", "CVE-2026-44405")
PARAMIKO_EXCEPTION_EXPIRES = date(2026, 8, 31)


def build_pip_audit_command(
    extra_args: list[str] | None = None,
    *,
    requirements: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
    ]
    if requirements is None:
        command.append("--local")
    else:
        command.extend(("--requirement", str(requirements), "--disable-pip"))
    for vulnerability_id in PARAMIKO_EXCEPTION_IDS:
        command.extend(("--ignore-vuln", vulnerability_id))
    command.extend(extra_args or [])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit the resolved release environment with an expiring compatibility exception."
    )
    parser.add_argument(
        "--format",
        choices=("columns", "json", "cyclonedx-json", "cyclonedx-xml", "markdown"),
        default=None,
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=None,
        help=(
            "Audit a fully hashed requirements lock instead of the current environment. "
            "Use this when generating a runtime dependency SBOM."
        ),
    )
    args = parser.parse_args(argv)

    today = date.today()
    if today > PARAMIKO_EXCEPTION_EXPIRES:
        print(
            (
                "The temporary Paramiko/Netmiko vulnerability exception expired on "
                f"{PARAMIKO_EXCEPTION_EXPIRES.isoformat()}. Review the upstream compatibility "
                "status before releasing."
            ),
            file=sys.stderr,
        )
        return 2

    if args.requirements is not None and not args.requirements.is_file():
        parser.error(f"requirements lock does not exist: {args.requirements}")

    print(
        (
            "Temporary audit exception: Paramiko GHSA-r374-rxx8-8654 "
            f"(expires {PARAMIKO_EXCEPTION_EXPIRES.isoformat()}; Netmiko 4.7 requires Paramiko <5)."
        ),
        file=sys.stderr,
    )
    extra_args: list[str] = []
    if args.format:
        extra_args.extend(("--format", args.format))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        extra_args.extend(("--output", str(args.output)))
    return subprocess.run(
        build_pip_audit_command(extra_args, requirements=args.requirements),
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
