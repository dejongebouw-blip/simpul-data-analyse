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


class RoutedTransport:
    """Serveert per route (padfragment) een eigen reeks vooraf gebouwde
    pagina's op ``?page=N``, zodat één client meerdere endpoints kan
    doorlopen zonder netwerk."""

    def __init__(self, routes: Mapping[str, Mapping[int, Any]]) -> None:
        self._routes = {path: dict(pages) for path, pages in routes.items()}
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def get(self, url: str, params: Optional[Mapping[str, Any]] = None) -> JSONResponse:
        query = dict(params or {})
        self.calls.append((url, query))
        for path, pages in self._routes.items():
            if path in url:
                page = int(query.get("page", 1))
                if page not in pages:
                    raise AssertionError(
                        f"onverwachte paginavraag voor {path}: page={page}; "
                        f"beschikbaar: {sorted(pages)}"
                    )
                return JSONResponse(pages[page])
        raise AssertionError(f"onverwachte route: {url}")

    def requested_pages_for(self, path: str) -> List[int]:
        return [
            int(query.get("page", 1))
            for url, query in self.calls
            if path in url
        ]


def fractal_page(
    *,
    data: List[Dict[str, Any]],
    current_page: int,
    total_pages: int,
    total: int,
    per_page: int = 50,
) -> Dict[str, Any]:
    return {
        "data": list(data),
        "meta": {
            "pagination": {
                "total": total,
                "count": len(data),
                "per_page": per_page,
                "current_page": current_page,
                "total_pages": total_pages,
            }
        },
    }


def synthetic_customer(i: int) -> Dict[str, Any]:
    return {
        "id": i,
        "name": f"Relatie {i}",
        "address": f"Laan {i}",
        "zipcode": f"{2000 + i} CD",
        "city": "Relatiestad",
        "email": f"relatie{i}@example.invalid",
        "phone": f"020-{i:07d}",
        "mobile": f"06-{i:08d}",
        "url_show": f"/customer/{i}",
        "text": f"relatienotitie {i}",
    }


def synthetic_project(i: int) -> Dict[str, Any]:
    """Projectitem met het genest meegeleverde relatie-object.

    Het geneste object draagt bewust géén ``id``: die bestaat niet in de
    bron-JSON, dus een koppeling naar ``customer.id`` valt niet te leggen.
    """
    return {
        "id": i,
        "name": f"Project {i}",
        "code": f"P-{i:04d}",
        "status": "actief",
        "url_show": f"/project/{i}",
        "customer": {
            "name": f"Genest bedrijf {i}",
            "address": f"Nestlaan {i}",
            "zipcode": f"{3000 + i} EF",
            "city": "Neststad",
        },
    }


def _chunk_fractal(
    rows: List[Dict[str, Any]],
    *,
    total: Optional[int],
    per_page: int,
) -> Dict[int, Dict[str, Any]]:
    chunks = [rows[i : i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    last = len(chunks)
    claimed_total = len(rows) if total is None else total
    return {
        number: fractal_page(
            data=chunk,
            current_page=number,
            total_pages=last,
            total=claimed_total,
            per_page=per_page,
        )
        for number, chunk in enumerate(chunks, start=1)
    }


def customer_pages(
    row_count: int,
    *,
    total: Optional[int] = None,
    per_page: int = 50,
    mutate=None,
) -> Dict[int, Dict[str, Any]]:
    """Fractal-paginareeks met ``row_count`` synthetische relaties."""
    rows = [synthetic_customer(i) for i in range(1, row_count + 1)]
    if mutate is not None:
        for row in rows:
            mutate(row)
    return _chunk_fractal(rows, total=total, per_page=per_page)


def project_pages(
    row_count: int,
    *,
    total: Optional[int] = None,
    per_page: int = 50,
    mutate=None,
) -> Dict[int, Dict[str, Any]]:
    """Fractal-paginareeks met ``row_count`` synthetische projecten."""
    rows = [synthetic_project(i) for i in range(1, row_count + 1)]
    if mutate is not None:
        for row in rows:
            mutate(row)
    return _chunk_fractal(rows, total=total, per_page=per_page)


def make_client(transport: Any):
    from simpul_extract.http_client import SimpulClient

    return SimpulClient(
        base_url="https://example.invalid",
        transport=transport,
        delay=0.0,
        sleep=lambda seconds: None,
    )
