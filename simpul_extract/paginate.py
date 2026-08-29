"""Pagineerlus achter één interface.

De aanroeper consumeert een iterator van :class:`Page`-waarden en hoeft de
pagineringsvorm niet te kennen: die vorm is een eigenschap van het endpoint,
niet van de aanroeper. Twee vormen zijn ingevuld: de Laravel-paginatorvorm
(``current_page``/``last_page`` op het topniveau, zoals ``/supplier.json``)
en de Fractal-vorm (``meta.pagination`` met ``current_page``/``total_pages``,
zoals ``/customer/all.json`` en ``/project/all.json``). Beide gebruiken
``?page=N`` en leveren dezelfde :class:`Page`-waarden op.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, List, Mapping, Optional


@dataclass(frozen=True)
class Page:
    """Eén opgehaalde pagina: de ruwe items plus het door de bron gemelde totaal."""

    items: List[Any]
    total: Optional[int]


def laravel_pages(
    client: Any, path: str, params: Optional[Mapping[str, Any]] = None
) -> Iterator[Page]:
    """Doorloopt een Laravel-paginator van pagina 1 tot en met ``last_page``."""
    page_number = 1
    while True:
        query = dict(params or {})
        query["page"] = page_number
        payload = client.get(path, params=query).json()
        total = payload.get("total")
        yield Page(
            items=list(payload.get("data") or []),
            total=None if total is None else int(total),
        )
        current = int(payload["current_page"])
        last = int(payload["last_page"])
        if current >= last:
            return
        page_number = current + 1


def fractal_pages(
    client: Any, path: str, params: Optional[Mapping[str, Any]] = None
) -> Iterator[Page]:
    """Doorloopt een Fractal-paginator (``meta.pagination``) tot ``total_pages``."""
    page_number = 1
    while True:
        query = dict(params or {})
        query["page"] = page_number
        payload = client.get(path, params=query).json()
        pagination = payload["meta"]["pagination"]
        total = pagination.get("total")
        yield Page(
            items=list(payload.get("data") or []),
            total=None if total is None else int(total),
        )
        current = int(pagination["current_page"])
        last = int(pagination["total_pages"])
        if current >= last:
            return
        page_number = current + 1
