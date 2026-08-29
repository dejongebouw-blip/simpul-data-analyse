import subprocess
import sys
import unittest

import simpul_extract
from simpul_extract.__main__ import main


class TestEntrypoint(unittest.TestCase):
    def test_package_imports(self):
        self.assertIsNotNone(simpul_extract)

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_help_via_module_invocation(self):
        result = subprocess.run(
            [sys.executable, "-m", "simpul_extract", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
