"""Pagineerlus achter één interface.

De aanroeper consumeert een iterator van :class:`Page`-waarden en kiest de
pagineringsvorm niet: die vorm is een eigenschap van het endpoint, niet van
de aanroeper. :func:`paginate` herkent per pagina welke vorm de respons
gebruikt en loopt door tot het einde:

- Fractal — ``meta.pagination`` met ``current_page``/``total_pages``, zoals
  ``/customer/all.json`` en ``/project/all.json``.
- Laravel-paginator — ``current_page``/``last_page`` op het topniveau,
  zoals ``/supplier.json``.

Beide vormen gebruiken ``?page=N`` en leveren dezelfde :class:`Page`-waarden
op. De HTTP-client (issue 03) wordt geïnjecteerd, zodat deze lus zonder
netwerk te toetsen is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, List, Mapping, Optional


@dataclass(frozen=True)
class Page:
    """Eén opgehaalde pagina: de ruwe items plus het door de bron gemelde totaal."""

    items: List[Any]
    total: Optional[int]


def _page_bounds(payload: Mapping[str, Any]) -> tuple:
    """Leest current/last/total uit de respons, vorm-onafhankelijk.

    Herkent de Fractal-vorm aan ``meta.pagination``; valt anders terug op de
    Laravel-vorm (``current_page``/``last_page`` op het topniveau).
    """
    pagination = payload.get("meta", {}).get("pagination") if isinstance(payload.get("meta"), Mapping) else None
    if pagination is not None:
        current = int(pagination["current_page"])
        last = int(pagination["total_pages"])
        total = pagination.get("total")
    else:
        current = int(payload["current_page"])
        last = int(payload["last_page"])
        total = payload.get("total")
    return current, last, (None if total is None else int(total))


def paginate(client: Any, path: str, params: Optional[Mapping[str, Any]] = None) -> Iterator[Page]:
    """Doorloopt een endpoint van pagina 1 tot en met de laatste pagina.

    Werkt onveranderd voor beide pagineringsvormen; de aanroeper hoeft het
    verschil niet te kennen.
    """
    page_number = 1
    while True:
        query = dict(params or {})
        query["page"] = page_number
        payload = client.get(path, params=query).json()
        current, last, total = _page_bounds(payload)
        yield Page(items=list(payload.get("data") or []), total=total)
        if current >= last:
            return
        page_number = current + 1
