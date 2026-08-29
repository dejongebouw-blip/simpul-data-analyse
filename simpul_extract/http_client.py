"""GET-only HTTP-naad naar Simpul.

Deze module bevat de enige lijn die met Simpul praat. Het publieke oppervlak
is bewust minimaal: één methode, ``get(path, params)``. Er is geen ``post``,
``put``, ``patch`` of ``delete`` en geen generieke request-dispatcher.

Verzoeken zijn strikt serieel. Tussen twee ``get``-aanroepen wacht de client
een instelbare pauze (``delay``). Op HTTP 429 en 5xx volgt exponentiële
backoff met maximaal drie pogingen; daarna gooit de client
:class:`SimpulHTTPError`, met route en statuscode in de melding, en stopt
de omliggende run met een niet-nul exit code.

De transportlaag (een ``requests.Session`` in productie, een stub in tests)
wordt geïnjecteerd. Zo kunnen pagineer- en sessielagen deze client als
afhankelijkheid krijgen en zonder netwerk getoetst worden.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional


DEFAULT_DELAY_SECONDS = 0.3
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_SECONDS = 1.0


class SimpulHTTPError(RuntimeError):
    """De bron blijft weigeren; de client geeft op na ``attempts`` pogingen."""

    def __init__(self, *, path: str, status_code: int, attempts: int) -> None:
        self.path = path
        self.status_code = status_code
        self.attempts = attempts
        super().__init__(
            f"Simpul weigerde na {attempts} pogingen: HTTP {status_code} voor {path}"
        )


class SimpulClient:
    """GET-only client naar Simpul met seriële belasting en backoff."""

    def __init__(
        self,
        *,
        base_url: str,
        transport: Any,
        delay: float = DEFAULT_DELAY_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts moet minstens 1 zijn")
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._delay = delay
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._sleep = sleep
        self._made_request = False

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        if self._made_request:
            self._sleep(self._delay)
        self._made_request = True

        url = self._full_url(path)
        for attempt in range(1, self._max_attempts + 1):
            response = self._transport.get(url, params=params)
            status = response.status_code
            if _is_retryable(status):
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_base * (2 ** (attempt - 1)))
                    continue
                raise SimpulHTTPError(
                    path=path, status_code=status, attempts=attempt
                )
            return response

    def _full_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self._base_url + path


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600
