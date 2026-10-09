"""Provision the isolated legacy SSH runtime without changing the app venv."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import os
import venv


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=root / ".runtime" / "legacy-ssh")
    args = parser.parse_args()
    destination = args.destination.resolve()
    venv.EnvBuilder(with_pip=True).create(destination)
    python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([
        str(python), "-m", "pip", "install", "--require-hashes", "--only-binary=:all:",
        "-r", str(root / "requirements-legacy-ssh-lock.txt"),
    ], check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    print(f"Legacy SSH Python: {python}")
    print("Custom location: set NETOPS_LEGACY_SSH_PYTHON to this absolute path before starting NetOps Suite.")


if __name__ == "__main__":
    main()
