"""Volledigheidscontrole en auditregel per entiteit (issue 08).

Per entiteit vergelijkt dit het aantal rijen dat de schrijflaag (issue 07)
daadwerkelijk heeft weggeschreven met het totaal dat de bron zelf meldt via
`Page.total` (issue 05) — nooit met een in de code vastgelegde peilwaarde.
De peilwaarden 951/2793/98 uit het PRD zijn een verwachting voor de PO, geen
assertie in dit codepad: de bron mag groeien.

Schrijft ook bij een falende controle een auditregel naar
`simpul_raw.extraction_run`, via dezelfde `upsert()`-interface als de
entiteitstabellen zelf: de ronde die het meest de moeite waard is om na te
kijken is precies de ronde die anders niets zou vastleggen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from simpul_extract.paginate import Page
from simpul_extract.storage import UpsertStore

EXIT_OK = 0
EXIT_INCOMPLETE = 3

EXTRACTION_RUN_TABLE = "extraction_run"


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompletenessError(Exception):
    """Geworpen wanneer het aantal weggeschreven rijen voor een entiteit niet
    overeenkomt met het door de bron gemelde totaal. Draagt `entity`,
    `rows_stored` en `source_total` zodat de aanroeper (issue 10) ze in de
    eindmelding kan zetten zonder de tekst opnieuw te moeten parsen."""

    def __init__(self, entity: str, rows_stored: int, source_total: Optional[int]):
        self.entity = entity
        self.rows_stored = rows_stored
        self.source_total = source_total
        self.exit_code = EXIT_INCOMPLETE
        super().__init__(
            f"{entity}: {rows_stored} rijen weggeschreven, bron meldt "
            f"{source_total} — volledigheidscontrole gefaald"
        )


def _extraction_run_row(
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    entity: str,
    rows_stored: int,
    source_total: Optional[int],
    note: Optional[str],
) -> Dict[str, Any]:
    # Géén 'id': `extraction_run.id` is een `bigint generated always as
    # identity` (issue 11), een kolom waar per definitie niet in geschreven
    # mag worden. Postgres vult hem zelf. Eén regel per entiteit per ronde
    # volgt uit `run_id` + `entity`, niet uit een door ons verzonnen sleutel.
    #
    # Géén 'complete' om dezelfde reden: die kolom is
    # `generated always as (source_total is not null and rows_stored =
    # source_total) stored`. De database rekent hem uit de twee getallen in
    # deze rij, zodat het vinkje nooit in tegenspraak kan zijn met de
    # aantallen ernaast. Meesturen levert PostgREST-fout 428C9 op, en dat is
    # precies wat ronde 3 van 2026-08-29 liet zien.
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "entity": entity,
        "rows_stored": rows_stored,
        "source_total": source_total,
        "note": note,
    }


def write_extraction_run(store: UpsertStore, **kwargs: Any) -> None:
    """Voegt één auditregel toe aan `extraction_run`. Bewust `append()` en
    niet `upsert()`: de tabel heeft een identity-`id` en geen `fetched_at`,
    dus de upsert-weg werkt hier niet. `complete` gaat niet mee — de database
    leidt die af; zie `_extraction_run_row`."""
    store.append(EXTRACTION_RUN_TABLE, [_extraction_run_row(**kwargs)])


def run_entity_round(
    *,
    entity: str,
    table: str,
    pages: Iterable[Page],
    parse: Callable[[Sequence[Any]], List[Mapping[str, Any]]],
    store: UpsertStore,
    run_id: str,
    now: Callable[[], str] = _default_now,
) -> int:
    """Doorloopt `pages`, parseert en schrijft elke pagina naar `table`, en
    vergelijkt na afloop het aantal weggeschreven rijen met het laatst door
    de bron gemelde totaal.

    `rows_stored` telt elke rij die aan `store.upsert()` is aangeboden, niet
    alleen de rijen die `inserted`/`updated` opleverden: een tweede,
    ongewijzigde ronde (SC-4) upsert dezelfde rijen zonder dat er iets
    verandert, en moet daarom nog steeds als volledig gelden.

    Schrijft ALTIJD een auditregel naar `extraction_run`, ook als de
    controle faalt — anders is de enige ronde waarvan je wilt weten wat er
    misging precies de ronde die niets vastlegt. Werpt daarna
    `CompletenessError` (met `entity`, het gevonden aantal en het gemelde
    totaal in de melding) zodra het aantal afwijkt. Retourneert EXIT_OK als
    het aantal klopt.
    """
    started_at = now()
    rows_stored = 0
    source_total: Optional[int] = None

    for page in pages:
        if page.total is not None:
            source_total = page.total
        rows = parse(page.items)
        store.upsert(table, rows)
        rows_stored += len(rows)

    finished_at = now()
    # Dezelfde som die `extraction_run.complete` in de database is. Hier
    # nodig voor `note` en voor de CompletenessError; niet om weg te
    # schrijven.
    complete = source_total is not None and rows_stored == source_total
    note = None if complete else f"{rows_stored} weggeschreven, bron meldt {source_total}"

    write_extraction_run(
        store,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        entity=entity,
        rows_stored=rows_stored,
        source_total=source_total,
        note=note,
    )

    if not complete:
        raise CompletenessError(entity, rows_stored, source_total)
    return EXIT_OK
