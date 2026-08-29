"""Synthetische stubs en fixtures voor de leveranciers-doorsnede.

Alle rijen hier zijn handgemaakt of gegenereerd — geen gedumpte
productierespons. Geen enkel testgeval doet een echt netwerkverzoek: de
transportlaag van de client wordt vervangen door :class:`PagedTransport`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple


class JSONResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class PagedTransport:
    """Serveert vooraf gebouwde Laravel-paginatorpagina's op ``?page=N``."""

    def __init__(self, pages: Mapping[int, Any]) -> None:
        self._pages = dict(pages)
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def get(self, url: str, params: Optional[Mapping[str, Any]] = None) -> JSONResponse:
        query = dict(params or {})
        self.calls.append((url, query))
        page = int(query.get("page", 1))
        if page not in self._pages:
            raise AssertionError(
                f"onverwachte paginavraag: page={page}; "
                f"beschikbaar: {sorted(self._pages)}"
            )
        return JSONResponse(self._pages[page])

    def requested_pages(self) -> List[int]:
        return [int(query.get("page", 1)) for _, query in self.calls]


class OnlyUpsertWriter:
    """Minimale schrijfnaad: bewijst dat de pipeline niets anders nodig heeft
    dan ``upsert(table, rows)``."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, List[Dict[str, Any]]]] = []

    def upsert(self, table: str, rows) -> None:
        self.calls.append((table, [dict(row) for row in rows]))


class TickingClock:
    """Deterministische klok: elke aanroep is één seconde later."""

    def __init__(
        self,
        start: datetime = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc),
    ) -> None:
        self._current = start

    def __call__(self) -> datetime:
        value = self._current
        self._current = self._current + timedelta(seconds=1)
        return value


def laravel_page(
    *,
    data: List[Dict[str, Any]],
    current_page: int,
    last_page: int,
    total: int,
    per_page: int = 50,
) -> Dict[str, Any]:
    return {
        "current_page": current_page,
        "data": list(data),
        "last_page": last_page,
        "per_page": per_page,
        "total": total,
    }


def synthetic_supplier(i: int) -> Dict[str, Any]:
    return {
        "id": i,
        "name": f"Leverancier {i}",
        "address": f"Straat {i}",
        "zipcode": f"{1000 + i} AB",
        "city": "Teststad",
        "email": f"info{i}@example.invalid",
        "phone": f"010-{i:07d}",
        "mobile": f"06-{i:08d}",
        "url_show": f"/supplier/{i}",
        "text": f"notitie {i}",
    }


def supplier_pages(
    row_count: int,
    *,
    total: Optional[int] = None,
    per_page: int = 50,
    mutate=None,
) -> Dict[int, Dict[str, Any]]:
    """Bouwt een volledige Laravel-paginatorreeks met ``row_count`` rijen.

    ``total`` mag afwijken van ``row_count`` om een bron te simuleren die
    meer rijen meldt dan hij levert (de tegen-pin voor SC-2). ``mutate``
    krijgt elke rij en mag velden aanpassen (voor de tweede-run-fixtures).
    """
    rows = [synthetic_supplier(i) for i in range(1, row_count + 1)]
    if mutate is not None:
        for row in rows:
            mutate(row)
    chunks = [rows[i : i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    last = len(chunks)
    claimed_total = row_count if total is None else total
    return {
        number: laravel_page(
            data=chunk,
            current_page=number,
            last_page=last,
            total=claimed_total,
            per_page=per_page,
        )
        for number, chunk in enumerate(chunks, start=1)
    }


def make_client(transport: PagedTransport):
    from simpul_extract.http_client import SimpulClient

    return SimpulClient(
        base_url="https://example.invalid",
        transport=transport,
        delay=0.0,
        sleep=lambda seconds: None,
    )
