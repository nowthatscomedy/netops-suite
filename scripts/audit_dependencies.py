from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


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
    command.extend(extra_args or [])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit the resolved release environment for known vulnerabilities."
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

    if args.requirements is not None and not args.requirements.is_file():
        parser.error(f"requirements lock does not exist: {args.requirements}")

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
