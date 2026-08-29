"""Toetst het backoff-gedrag van de Simpul-client.

Vereisten uit issue 03:
  - Op 429 en 5xx exponentiële backoff met **maximaal drie pogingen**.
  - Daarna stopt de run met een **niet-nul exit code** en een melding die
    route en statuscode noemt.
  - Verzoeken zijn strikt serieel: tussen twee ``get``-aanroepen wacht de
    client de ingestelde ``delay``.
  - De transportlaag wordt geïnjecteerd — geen echt netwerkverkeer.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from simpul_extract.http_client import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    SimpulClient,
    SimpulHTTPError,
)


class _StubResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _StubTransport:
    """Retourneert vaste statuscodes; breekt hard als de client vaker roept dan mag."""

    def __init__(self, statuses: Sequence[int], hard_limit: int = 10) -> None:
        self._statuses = list(statuses)
        self._hard_limit = hard_limit
        self.calls: List[Tuple[str, Optional[Mapping[str, Any]]]] = []

    def get(
        self, url: str, params: Optional[Mapping[str, Any]] = None
    ) -> _StubResponse:
        if len(self.calls) >= self._hard_limit:
            raise AssertionError(
                f"transport aangeroepen meer dan {self._hard_limit} keer; "
                "de client respecteert max_attempts niet"
            )
        self.calls.append((url, params))
        idx = min(len(self.calls) - 1, len(self._statuses) - 1)
        return _StubResponse(self._statuses[idx])


class _RecordingSleep:
    def __init__(self) -> None:
        self.sleeps: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class TestModuleDefaults(unittest.TestCase):
    def test_delay_default_is_zero_point_three_seconds(self) -> None:
        self.assertEqual(DEFAULT_DELAY_SECONDS, 0.3)

    def test_max_attempts_default_is_three(self) -> None:
        self.assertEqual(DEFAULT_MAX_ATTEMPTS, 3)


class TestGivesUpAfterThreeAttempts(unittest.TestCase):
    def _client(
        self, transport: _StubTransport, sleep: _RecordingSleep
    ) -> SimpulClient:
        return SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=1.0,
            sleep=sleep,
        )

    def test_stops_after_three_attempts_on_persistent_429(self) -> None:
        transport = _StubTransport([429])
        sleep = _RecordingSleep()
        client = self._client(transport, sleep)
        with self.assertRaises(SimpulHTTPError):
            client.get("/incidents")
        self.assertEqual(
            len(transport.calls),
            3,
            msg=(
                "verwacht exact 3 pogingen; kreeg "
                f"{len(transport.calls)}. Bij onbeperkte pogingen zou de "
                "stub hard breken op de vierde call."
            ),
        )

    def test_stops_after_three_attempts_on_persistent_500(self) -> None:
        transport = _StubTransport([500])
        sleep = _RecordingSleep()
        client = self._client(transport, sleep)
        with self.assertRaises(SimpulHTTPError):
            client.get("/incidents")
        self.assertEqual(len(transport.calls), 3)

    def test_stops_after_three_attempts_on_503(self) -> None:
        transport = _StubTransport([503])
        sleep = _RecordingSleep()
        client = self._client(transport, sleep)
        with self.assertRaises(SimpulHTTPError):
            client.get("/incidents")
        self.assertEqual(len(transport.calls), 3)


class TestBackoffIsExponential(unittest.TestCase):
    def test_two_backoff_pauses_between_three_attempts(self) -> None:
        transport = _StubTransport([503])
        sleep = _RecordingSleep()
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=1.0,
            sleep=sleep,
        )
        with self.assertRaises(SimpulHTTPError):
            client.get("/incidents")
        # 3 pogingen → 2 backoff-slaapjes ertussen. delay=0.0 telt niet mee.
        nonzero = [s for s in sleep.sleeps if s > 0]
        self.assertEqual(
            len(nonzero),
            2,
            msg=f"verwacht 2 backoff-slaapjes tussen 3 pogingen; kreeg {sleep.sleeps}",
        )

    def test_backoff_grows_between_successive_attempts(self) -> None:
        transport = _StubTransport([503])
        sleep = _RecordingSleep()
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=1.0,
            sleep=sleep,
        )
        with self.assertRaises(SimpulHTTPError):
            client.get("/incidents")
        nonzero = [s for s in sleep.sleeps if s > 0]
        self.assertGreater(
            nonzero[1],
            nonzero[0],
            msg=(
                "backoff moet exponentieel toenemen; "
                f"kreeg opeenvolgende pauzes {nonzero}"
            ),
        )


class TestRetryStopsOnSuccessOrNonRetryable(unittest.TestCase):
    def test_success_after_retry_returns_response_and_stops(self) -> None:
        transport = _StubTransport([429, 200])
        sleep = _RecordingSleep()
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=1.0,
            sleep=sleep,
        )
        response = client.get("/incidents")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(transport.calls), 2)

    def test_non_retryable_status_is_returned_directly(self) -> None:
        transport = _StubTransport([404])
        sleep = _RecordingSleep()
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=1.0,
            sleep=sleep,
        )
        response = client.get("/incidents")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            len(transport.calls),
            1,
            msg="4xx (behalve 429) hoort niet herhaald te worden",
        )


class TestErrorMessageNamesRouteAndStatus(unittest.TestCase):
    def test_message_contains_path(self) -> None:
        transport = _StubTransport([503])
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=0.0,
            sleep=lambda s: None,
        )
        with self.assertRaises(SimpulHTTPError) as cm:
            client.get("/health-check")
        self.assertIn("/health-check", str(cm.exception))

    def test_message_contains_status_code(self) -> None:
        transport = _StubTransport([503])
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            backoff_base_seconds=0.0,
            sleep=lambda s: None,
        )
        with self.assertRaises(SimpulHTTPError) as cm:
            client.get("/health-check")
        self.assertIn("503", str(cm.exception))


class TestSerialDelayBetweenCalls(unittest.TestCase):
    def test_first_call_has_no_delay(self) -> None:
        transport = _StubTransport([200])
        sleep = _RecordingSleep()
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.3,
            sleep=sleep,
        )
        client.get("/one")
        self.assertEqual(sleep.sleeps, [])

    def test_second_call_waits_configured_delay(self) -> None:
        transport = _StubTransport([200, 200])
        sleep = _RecordingSleep()
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.42,
            sleep=sleep,
        )
        client.get("/one")
        client.get("/two")
        self.assertEqual(
            sleep.sleeps,
            [0.42],
            msg="tweede call moet exact één delay-slaap doen",
        )


class TestClientIsInjectable(unittest.TestCase):
    """Bewijs dat volgende lagen de client als afhankelijkheid kunnen krijgen
    zonder netwerk aan te raken."""

    def test_no_real_network_used_in_tests(self) -> None:
        transport = _StubTransport([200])
        client = SimpulClient(
            base_url="https://example.invalid",
            transport=transport,
            delay=0.0,
            sleep=lambda s: None,
        )

        def dependent_layer(inner_client: SimpulClient) -> Any:
            return inner_client.get("/needed-by-later-layer")

        response = dependent_layer(client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(transport.calls), 1)


class TestRunStopsWithNonZeroExitCode(unittest.TestCase):
    """Bevestigt dat, wanneer de bron blijft weigeren, de omliggende run stopt
    met een niet-nul exit code en een begrijpelijke melding.

    We draaien een subprocess dat de client tegen een blijvend-429-stub gebruikt
    en de opgegooide SimpulHTTPError afvangt zoals een normale entry point dat
    zou doen: melding op stderr, ``sys.exit`` met niet-nul code.
    """

    def test_process_exits_nonzero_after_max_attempts(self) -> None:
        script = (
            "import sys\n"
            "from simpul_extract.http_client import SimpulClient, SimpulHTTPError\n"
            "\n"
            "class _Response:\n"
            "    def __init__(self, status_code):\n"
            "        self.status_code = status_code\n"
            "\n"
            "class _AlwaysBusyTransport:\n"
            "    def get(self, url, params=None):\n"
            "        return _Response(429)\n"
            "\n"
            "client = SimpulClient(\n"
            "    base_url='https://example.invalid',\n"
            "    transport=_AlwaysBusyTransport(),\n"
            "    delay=0.0,\n"
            "    backoff_base_seconds=0.0,\n"
            "    sleep=lambda s: None,\n"
            ")\n"
            "try:\n"
            "    client.get('/incidents')\n"
            "except SimpulHTTPError as exc:\n"
            "    print(f'simpul-extract: {exc}', file=sys.stderr)\n"
            "    sys.exit(3)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            msg=(
                "verwacht niet-nul exit code na drie mislukte pogingen; "
                f"kreeg {proc.returncode}.\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            ),
        )
        combined = proc.stdout + proc.stderr
        self.assertIn(
            "429",
            combined,
            msg="stopmelding moet de statuscode noemen",
        )
        self.assertIn(
            "/incidents",
            combined,
            msg="stopmelding moet de route noemen",
        )


if __name__ == "__main__":
    unittest.main()
