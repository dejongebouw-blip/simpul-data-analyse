"""Inspecteert het gebouwde image van buitenaf op geheimen en bedrijfsdata.

Draait bewust op de host (niet in het image) en gebruikt uitsluitend de
standaardbibliotheek: subprocess om te bouwen en te inspecteren, json om
`docker image inspect` te lezen en tarfile om de filesystem-laag na te lopen
zonder het image te draaien.
"""

import io
import json
import re
import subprocess
import tarfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_TAG = "simpul-extract-secrets-test"

FORBIDDEN_ENV_NAMES = (
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "SIMPUL_USERNAME",
    "SIMPUL_PASSWORD",
)


class TestImageSecrets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = subprocess.run(
            ["docker", "build", "-t", IMAGE_TAG, str(REPO_ROOT)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            raise RuntimeError(
                f"docker build faalde:\n{build.stdout}\n{build.stderr}"
            )

        inspect = subprocess.run(
            ["docker", "image", "inspect", IMAGE_TAG],
            capture_output=True,
            text=True,
            check=True,
        )
        cls.inspect_data = json.loads(inspect.stdout)[0]

        history = subprocess.run(
            ["docker", "history", "--no-trunc", "--format", "{{json .}}", IMAGE_TAG],
            capture_output=True,
            text=True,
            check=True,
        )
        cls.history_entries = [
            json.loads(line) for line in history.stdout.splitlines() if line.strip()
        ]

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            ["docker", "image", "rm", "-f", IMAGE_TAG],
            capture_output=True,
        )

    def test_geen_geheime_env_in_finale_config(self):
        env_entries = self.inspect_data.get("Config", {}).get("Env") or []
        keys = [entry.split("=", 1)[0] for entry in env_entries]
        for name in FORBIDDEN_ENV_NAMES:
            self.assertNotIn(
                name, keys,
                f"Config.Env van het image bevat {name}",
            )

    def test_geen_geheime_env_of_arg_laag_in_historie(self):
        for name in FORBIDDEN_ENV_NAMES:
            pattern = re.compile(r"\b(ENV|ARG)\s+" + re.escape(name) + r"\b")
            for layer in self.history_entries:
                created_by = layer.get("CreatedBy", "")
                self.assertIsNone(
                    pattern.search(created_by),
                    f"Imagehistorie bevat een ENV/ARG-laag met {name}: {created_by!r}",
                )

    def test_geen_env_bestand_of_fixture_in_image(self):
        create = subprocess.run(
            ["docker", "create", IMAGE_TAG],
            capture_output=True,
            text=True,
            check=True,
        )
        container_id = create.stdout.strip()
        try:
            export = subprocess.run(
                ["docker", "export", container_id],
                capture_output=True,
                check=True,
            )
            with tarfile.open(fileobj=io.BytesIO(export.stdout), mode="r:") as tar:
                names = tar.getnames()
        finally:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)

        for name in names:
            basename = name.rsplit("/", 1)[-1]
            self.assertNotEqual(
                basename, ".env",
                f"Image bevat een .env-bestand: {name}",
            )
            self.assertNotIn(
                "tests/fixtures/", name,
                f"Image bevat een testfixture: {name}",
            )


if __name__ == "__main__":
    unittest.main()
