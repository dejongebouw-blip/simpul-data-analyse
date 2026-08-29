"""Ingang voor `python3 -m simpul_extract`.

De ingang kent in dit issue alleen `--delay` en valideert dat de
Supabase-bestemming via omgevingsvariabelen beschikbaar is. Er wordt
nog niets opgehaald of geschreven.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Iterable, Mapping, Optional

from simpul_extract.http_client import DEFAULT_DELAY_SECONDS, SimpulHTTPError

REQUIRED_ENV_VARS = ("SUPABASE_URL", "SUPABASE_SECRET_KEY")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m simpul_extract",
        description="Extractor voor Simpul incidenten (containerdraaibaar).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        metavar="SECONDS",
        help=(
            "Wachttijd in seconden tussen HTTP-verzoeken "
            f"(standaard: {DEFAULT_DELAY_SECONDS})."
        ),
    )
    return parser


def find_missing_env(env: Mapping[str, str]) -> Optional[str]:
    for name in REQUIRED_ENV_VARS:
        value = env.get(name)
        if value is None or value == "":
            return name
    return None


def main(
    argv: Optional[Iterable[str]] = None,
    run: Optional[Callable[[argparse.Namespace], int]] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    missing = find_missing_env(os.environ)
    if missing is not None:
        # Alleen de naam van de ontbrekende variabele, nooit een waarde.
        sys.stderr.write(
            f"error: verplichte omgevingsvariabele {missing} ontbreekt of is leeg\n"
        )
        return 2

    # `run` is het injectiepunt voor de ophaallagen uit latere issues.
    if run is None:
        return 0
    try:
        return int(run(args))
    except SimpulHTTPError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
