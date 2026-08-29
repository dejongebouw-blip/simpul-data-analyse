"""Pagineerlus achter één interface.

De aanroeper consumeert een iterator van :class:`Page`-waarden en hoeft de
pagineringsvorm niet te kennen: die vorm is een eigenschap van het endpoint,
niet van de aanroeper. Hier is de Laravel-paginatorvorm ingevuld
(``current_page``/``last_page`` met ``?page=N``, zoals ``/supplier.json``);
issue 05 voegt de Fractal-vorm toe zonder de aanroeper te wijzigen.
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
