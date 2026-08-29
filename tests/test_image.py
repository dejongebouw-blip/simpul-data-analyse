"""Host-side tests voor het containercontract.

Alleen stdlib: de host draait Python 3.9 zonder extra pakketten. Alle
pakket-tests draaien in het image via `tests/run_in_image.py`.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_TAG = "simpul-extract-test:01"


def _build_image() -> None:
    proc = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker build faalde (exit {proc.returncode}):\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


class TestImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _build_image()

    def test_help_exits_zero_and_mentions_delay(self) -> None:
        proc = subprocess.run(
            ["docker", "run", "--rm", IMAGE_TAG, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"--help gaf exit {proc.returncode}\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        self.assertIn("--delay", proc.stdout)

    def test_runs_as_nonroot_user(self) -> None:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "id", IMAGE_TAG, "-u"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        uid_str = proc.stdout.strip()
        self.assertTrue(
            uid_str.isdigit(),
            msg=f"verwacht numeriek uid, kreeg {uid_str!r}",
        )
        self.assertNotEqual(
            int(uid_str),
            0,
            msg="container mag niet als root (uid 0) draaien",
        )

    def test_uid_is_stable_across_runs(self) -> None:
        first = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "id", IMAGE_TAG, "-u"],
            capture_output=True, text=True,
        )
        second = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "id", IMAGE_TAG, "-u"],
            capture_output=True, text=True,
        )
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        self.assertEqual(
            first.stdout.strip(),
            second.stdout.strip(),
            msg="uid moet vast zijn over meerdere runs",
        )


if __name__ == "__main__":
    unittest.main()
