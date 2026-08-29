"""De schrijfnaad: één schrijfoperatie, geen DDL.

Alles wat de extractie wegschrijft gaat door één injecteerbare interface met
precies één operatie: ``upsert(table, rows)``. De sleutel is ``id``: een rij
met een reeds bestaande ``id`` werkt de velden van de bestaande rij bij in
plaats van een duplicaat te maken. Rijen zonder ``id`` — het auditspoor
``extraction_run``, waar de database zelf de sleutel uitdeelt — worden
toegevoegd.

De echte implementatie tegen Supabase landt in issue 09; hier ligt alleen de
vorm vast, plus een in-memory nepimplementatie zodat tests zonder Postgres en
zonder netwerk draaien. De naad kent geen operatie om tabellen aan te maken,
te wijzigen of te legen; het schema is de verantwoordelijkheid van issue 09
en 10, niet van het extractiescript.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Protocol, Sequence


class SupportsUpsert(Protocol):
    """Het volledige schrijfoppervlak dat de extractie nodig heeft."""

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        ...


class InMemoryWriter:
    """Nepimplementatie van de schrijfnaad, voor tests."""

    def __init__(self) -> None:
        self._tables: Dict[str, List[Dict[str, Any]]] = {}

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        stored = self._tables.setdefault(table, [])
        for row in rows:
            incoming = dict(row)
            key = incoming.get("id")
            if key is None:
                stored.append(incoming)
                continue
            for existing in stored:
                if existing.get("id") == key:
                    existing.update(incoming)
                    break
            else:
                stored.append(incoming)

    def rows(self, table: str) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._tables.get(table, [])]
