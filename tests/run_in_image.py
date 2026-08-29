#!/usr/bin/env python3
"""Host-side runner: bouwt het image en draait een unittest-module daarin.

Gebruik:

    python3 tests/run_in_image.py <module>

De exit code van dit script is de exit code van `unittest` binnen de
container (of van `docker build` als die eerder faalt). Deze module gebruikt
uitsluitend stdlib zodat ze op de kale host draait (Python 3.9, geen extras).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_TAG = "simpul-extract-test:01"


def build_image() -> int:
    return subprocess.call(
        ["docker", "build", "-t", IMAGE_TAG, str(REPO_ROOT)]
    )


def run_module(module: str) -> int:
    tests_dir = REPO_ROOT / "tests"
    return subprocess.call([
        "docker", "run", "--rm",
        "-v", f"{tests_dir}:/app/tests:ro",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint", "python3",
        IMAGE_TAG,
        "-m", "unittest", module,
    ])


def main(argv):
    if len(argv) != 2:
        sys.stderr.write(f"usage: {argv[0]} <testmodule>\n")
        return 2
    module = argv[1]
    rc = build_image()
    if rc != 0:
        return rc
    return run_module(module)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
