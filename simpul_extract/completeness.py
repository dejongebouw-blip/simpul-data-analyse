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


class ConflictingDuplicateError(Exception):
    """Twee rijen met hetzelfde `id` maar verschillende inhoud.

    Ontdubbelen is alleen verdedigbaar omdat de dubbelen die de bron levert
    aantoonbaar identiek zijn (gemeten 2026-08-29: 15 dubbele projectid's,
    nul afwijkende velden). Zodra dat niet meer geldt is er een winnaar te
    kiezen, en dat is een gegevensbesluit dat deze code niet stilzwijgend mag
    nemen. Dan valt de ronde, met het id erbij.
    """

    def __init__(self, entity: str, row_id: Any):
        self.entity = entity
        self.row_id = row_id
        super().__init__(
            f"{entity}: id {row_id!r} komt meer dan eens voor met verschillende "
            f"inhoud — ontdubbelen zou een van de twee versies weggooien"
        )


def dedupe_by_id(rows: Sequence[Mapping[str, Any]], *, entity: str) -> tuple:
    """Ontdubbelt `rows` op `id` en telt hoeveel rijen er dubbel geleverd zijn.

    De bron deelt dezelfde entiteit soms twee keer uit: `/project/all.json`
    meldt 2793 rijen en levert er 2793, waarvan 2778 uniek. Postgres weigert
    zo'n batch met 21000 ("ON CONFLICT DO UPDATE command cannot affect row a
    second time"), want één command mag dezelfde rij niet twee keer raken.
    Ontdubbelen hoort daarom bij het ophalen, niet bij het wegschrijven.

    Behoudt de eerste rij per `id` en de volgorde van de bron. Verschillen de
    dubbelen inhoudelijk, dan werpt dit `ConflictingDuplicateError` in plaats
    van een winnaar te kiezen. Een rij zonder `id` gaat ongemoeid door: op een
    ontbrekende sleutel valt niets samen te voegen, en de schrijflaag weigert
    zo'n rij toch al.

    Retourneert `(unieke_rijen, aantal_dubbel_geleverd)`.
    """
    unique: Dict[Any, Mapping[str, Any]] = {}
    volgorde: List[Any] = []
    zonder_id: List[Mapping[str, Any]] = []
    duplicates = 0

    for row in rows:
        if "id" not in row:
            zonder_id.append(row)
            continue
        row_id = row["id"]
        if row_id not in unique:
            unique[row_id] = row
            volgorde.append(row_id)
            continue
        if unique[row_id] != row:
            raise ConflictingDuplicateError(entity, row_id)
        duplicates += 1

    return [unique[row_id] for row_id in volgorde] + zonder_id, duplicates


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
