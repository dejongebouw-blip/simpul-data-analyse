"""Stdlib-only runner: bouwt het simpul-extract image en draait een testmodule erin.

Gebruik: python3 tests/run_in_image.py <testmodule>
De host draagt Python 3.9 zonder third-party packages, dus dit script
gebruikt uitsluitend de standaardbibliotheek.
"""

import argparse
import subprocess
import sys
from pathlib import Path

IMAGE_TAG = "simpul-extract"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bouw het simpul-extract image en draai een unittest-module erin.",
    )
    parser.add_argument("module", help="Testmodule, bijv. tests.test_entrypoint")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent

    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(repo_root)],
    )
    if build.returncode != 0:
        return build.returncode

    run = subprocess.run(
        [
            "docker", "run", "--rm",
            "--network", "none",
            "--entrypoint", "python3",
            "-v", f"{repo_root}:/app:ro",
            "-w", "/app",
            IMAGE_TAG,
            "-m", "unittest", args.module,
        ],
    )
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
