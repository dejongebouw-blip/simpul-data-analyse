"""In-image tests voor de ingang van `simpul_extract`.

Deze module wordt binnen het gebouwde image gedraaid via
`tests/run_in_image.py tests.test_entrypoint`. In-image is Python 3.12
beschikbaar plus alles wat het pakket zelf importeert.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

from simpul_extract.__main__ import build_parser, find_missing_env


class TestArgumentParser(unittest.TestCase):
    def test_help_exits_zero_and_mentions_delay(self) -> None:
        parser = build_parser()
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            parser.parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--delay", buf.getvalue())

    def test_delay_is_accepted(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["--delay", "2.5"])
        self.assertEqual(ns.delay, 2.5)

    def test_delay_has_default(self) -> None:
        parser = build_parser()
        ns = parser.parse_args([])
        self.assertTrue(hasattr(ns, "delay"))

    def test_db_flag_is_rejected(self) -> None:
        parser = build_parser()
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stderr(stderr):
            parser.parse_args(["--db", "/tmp/x.sqlite"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_parser_has_no_db_option(self) -> None:
        parser = build_parser()
        option_strings = set()
        for action in parser._actions:
            option_strings.update(action.option_strings)
        self.assertNotIn("--db", option_strings)


class TestEnvironmentValidation(unittest.TestCase):
    """Toetst het gedrag van `python3 -m simpul_extract` bij ontbrekende env."""

    def _base_env(self) -> dict:
        keep = {}
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH"):
            value = os.environ.get(key)
            if value is not None:
                keep[key] = value
        return keep

    def _run(self, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "simpul_extract"],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_missing_url_fails_and_names_variable(self) -> None:
        env = self._base_env()
        secret_value = "sekret-waarde-mag-niet-lekken"
        env["SUPABASE_SECRET_KEY"] = secret_value
        proc = self._run(env)
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("SUPABASE_URL", combined)
        self.assertNotIn(secret_value, combined)

    def test_missing_secret_fails_and_names_variable(self) -> None:
        env = self._base_env()
        url_value = "https://voorbeeld-project.supabase.co"
        env["SUPABASE_URL"] = url_value
        proc = self._run(env)
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("SUPABASE_SECRET_KEY", combined)
        self.assertNotIn(url_value, combined)

    def test_both_missing_fails_nonzero(self) -> None:
        env = self._base_env()
        proc = self._run(env)
        self.assertNotEqual(proc.returncode, 0)

    def test_empty_string_counts_as_missing(self) -> None:
        env = self._base_env()
        env["SUPABASE_URL"] = ""
        env["SUPABASE_SECRET_KEY"] = "iets"
        proc = self._run(env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SUPABASE_URL", proc.stdout + proc.stderr)

    def test_valid_env_exits_zero(self) -> None:
        env = self._base_env()
        env["SUPABASE_URL"] = "https://voorbeeld.supabase.co"
        env["SUPABASE_SECRET_KEY"] = "dummy"
        proc = self._run(env)
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"verwacht exit 0 met beide env gezet;\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )


class TestFindMissingEnv(unittest.TestCase):
    def test_returns_first_missing(self) -> None:
        self.assertEqual(find_missing_env({}), "SUPABASE_URL")

    def test_returns_none_when_all_present(self) -> None:
        env = {"SUPABASE_URL": "u", "SUPABASE_SECRET_KEY": "s"}
        self.assertIsNone(find_missing_env(env))

    def test_empty_string_treated_as_missing(self) -> None:
        env = {"SUPABASE_URL": "", "SUPABASE_SECRET_KEY": "s"}
        self.assertEqual(find_missing_env(env), "SUPABASE_URL")


if __name__ == "__main__":
    unittest.main()
