"""Entry point voor het simpul_extract-container: bedraadt de lagen uit de
issues 03 t/m 09 tot één ronde over `customer`, `project` en `supplier`.

Vaste volgorde (zie ook `simpul_extract/session.py` voor de sessiestap):

  1. Cookiepot lezen en cookies injecteren.
  2. Sessie levend bewijzen; dood -> één loginpoging; mislukt -> EXIT_SESSION_LOST,
     niets geschreven.
  3. Per entiteit pagineren, parsen en ontdubbelen op `id` (nog niet
     wegschrijven): de bron deelt dezelfde entiteit soms twee keer uit.
  4. Relatiedetails ophalen voor `email` op de al geparste customer-rijen.
  5. Wegschrijven: elke entiteit in één upsert naar zijn tabel.
  6. Volledigheid toetsen per entiteit (rows_stored vs. het door de bron
     gemelde totaal, gecorrigeerd voor dubbel geleverde rijen) en de
     auditregel schrijven — ook bij een falende toets.
  7. Geroteerde cookie terugschrijven naar de pot, ongeacht de uitkomst van
     stap 6: een datafout mag de sessie niet kosten.

Alle afhankelijkheden (`client`, `session`, `pot`, `store`) zijn injecteerbaar
via `run()`, zodat een test de volledige ronde zonder netwerk, zonder
Postgres en zonder geldige sessie kan doorlopen.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO

from simpul_extract.completeness import EXIT_INCOMPLETE, dedupe_by_id, write_extraction_run
from simpul_extract.http_client import SimpulHTTPClient
from simpul_extract.parsers import (
    fetch_customer_emails,
    parse_customer_list,
    parse_project_list,
    parse_supplier_list,
)
from simpul_extract.paginate import paginate
from simpul_extract.session import (
    EXIT_OK,
    EXIT_SESSION_LOST,
    CookiePot,
    SessionError,
    SessionLostError,
    SessionRound,
    credentials_from_env,
)
from simpul_extract.observability import configure_logging, get_logger
from simpul_extract.storage import (
    SCHEMA,
    StorageError,
    postgrest_base_url,
    postgrest_store_from_env,
    raise_for_write,
)

logger = get_logger(__name__)

DEFAULT_PROBE_PATH = "/customer/all.json"


@dataclass(frozen=True)
class EntitySpec:
    """Eén van de drie entiteiten uit het PRD: naam, doeltabel, bronpad en parser."""

    name: str
    table: str
    path: str
    parse: Callable[[Iterable[Mapping[str, Any]]], List[Dict[str, Any]]]


ENTITIES: Sequence[EntitySpec] = (
    EntitySpec("customer", "customer", "/customer/all.json", parse_customer_list),
    EntitySpec("project", "project", "/project/all.json", parse_project_list),
    EntitySpec("supplier", "supplier", "/supplier.json", parse_supplier_list),
)


@dataclass(frozen=True)
class EntityOutcome:
    """Resultaat van één entiteit: wat is gevonden, wat meldt de bron, en
    of dat overeenkomt."""

    entity: str
    rows_stored: int
    source_total: Optional[int]
    complete: bool
    duplicates: int = 0


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_summary(outcomes: Sequence[EntityOutcome], email_found: int) -> str:
    """Bouwt het slotoverzicht: per entiteit gevonden/gemeld plus ok, het
    aantal gevonden e-mailadressen en de sessiestatus. Draagt nooit een
    cookiewaarde of wachtwoord."""
    lines = [
        f"{outcome.entity}: gevonden {outcome.rows_stored}, gemeld {outcome.source_total}"
        + (f", {outcome.duplicates} dubbel geleverd" if outcome.duplicates else "")
        + f" ({'ok' if outcome.complete else 'onvolledig'})"
        for outcome in outcomes
    ]
    lines.append(f"e-mailadressen gevonden: {email_found}")
    lines.append("sessie: ok")
    return "\n".join(lines)


def run(
    *,
    client: SimpulHTTPClient,
    session: Any,
    pot: CookiePot,
    store: Any,
    username: str,
    password: str,
    stdout: Optional[TextIO] = None,
    run_id: Optional[str] = None,
    now: Callable[[], str] = _default_now,
    probe_path: str = DEFAULT_PROBE_PATH,
) -> int:
    """Voert één volledige ronde uit over alle `ENTITIES`. Retourneert de
    exit code die de aanroeper (CLI of test) moet gebruiken."""
    out = stdout if stdout is not None else sys.stdout
    run_id = run_id or uuid.uuid4().hex

    logger.info("ronde start, run_id %s", run_id)
    session_round = SessionRound(client, session, pot, username, password)
    session_round.start()
    try:
        session_round.ensure_live(probe_path)
    except SessionLostError as exc:
        logger.error("sessie verloren: %s", exc)
        print("sessie: dood, login mislukt", file=out)
        return EXIT_SESSION_LOST
    logger.info("sessie leeft; begin met ophalen")

    collected: Dict[str, Dict[str, Any]] = {}
    for entity in ENTITIES:
        started_at = now()
        rows: List[Dict[str, Any]] = []
        source_total: Optional[int] = None
        pages = 0
        logger.info("%s: ophalen begint via %s", entity.name, entity.path)
        for page in paginate(client, entity.path):
            pages += 1
            if page.total is not None:
                source_total = page.total
            rows.extend(entity.parse(page.items))
            if pages % 10 == 0:
                logger.info(
                    "%s: %d pagina's, %d rijen (bron meldt %s)",
                    entity.name, pages, len(rows), source_total,
                )
        # De bron kan dezelfde entiteit twee keer uitdelen; Postgres weigert
        # zo'n batch met 21000. Ontdubbelen hoort daarom hier, bij het ophalen,
        # en niet bij het wegschrijven -- en het aantal reist mee, want het
        # corrigeert straks het door de bron gemelde totaal.
        rows, duplicates = dedupe_by_id(rows, entity=entity.name)
        if duplicates:
            logger.info(
                "%s: %d dubbel geleverde rijen ontdubbeld op id, %d uniek over",
                entity.name, duplicates, len(rows),
            )
        logger.info(
            "%s: ophalen klaar, %d pagina's, %d rijen, bron meldt %s",
            entity.name, pages, len(rows), source_total,
        )
        collected[entity.name] = {
            "rows": rows,
            "source_total": source_total,
            "duplicates": duplicates,
            "started_at": started_at,
        }

    logger.info(
        "detailpagina's ophalen voor %d relaties",
        len(collected["customer"]["rows"]),
    )
    email_found = fetch_customer_emails(client, collected["customer"]["rows"])
    logger.info("detailpagina's klaar, %d e-mailadressen gevonden", email_found)

    outcomes: List[EntityOutcome] = []
    incomplete = False
    for entity in ENTITIES:
        data = collected[entity.name]
        rows = data["rows"]
        store.upsert(entity.table, rows)
        rows_stored = len(rows)
        duplicates = data["duplicates"]
        gemeld_totaal = data["source_total"]
        # `complete` telt entiteiten, niet uitgedeelde rijen -- besluit van
        # 2026-08-29, zie adr/2026-08-29-volledig-telt-entiteiten.md. De bron
        # telt haar dubbel geleverde rijen mee in het gemelde totaal, dus dat
        # totaal wordt met exact dat aantal gecorrigeerd. Zonder die correctie
        # meldt elke ronde ONVOLLEDIG op een eigenaardigheid van de bron, en
        # een alarm dat altijd afgaat is geen alarm meer. Het rauwe getal
        # verdwijnt niet: het staat in `note`, ook bij een geslaagde ronde.
        source_total = gemeld_totaal
        if gemeld_totaal is not None and duplicates:
            source_total = gemeld_totaal - duplicates
        # Dezelfde som die `extraction_run.complete` in de database is: die
        # kolom is generated, dus hij gaat niet mee in de auditregel. Hier
        # nodig voor `note`, het slotoverzicht en de exitcode.
        complete = source_total is not None and rows_stored == source_total
        if not complete:
            incomplete = True
        finished_at = now()
        notities = []
        if duplicates:
            notities.append(
                f"bron meldde {gemeld_totaal}, {duplicates} rijen dubbel geleverd"
            )
        if not complete:
            notities.append(f"{rows_stored} weggeschreven, bron meldt {source_total}")
        note = "; ".join(notities) if notities else None
        write_extraction_run(
            store,
            run_id=run_id,
            started_at=data["started_at"],
            finished_at=finished_at,
            entity=entity.name,
            rows_stored=rows_stored,
            source_total=source_total,
            note=note,
        )
        logger.info(
            "%s: %d weggeschreven, bron meldt %s, %s",
            entity.name, rows_stored, source_total,
            "volledig" if complete else "ONVOLLEDIG",
        )
        outcomes.append(
            EntityOutcome(entity.name, rows_stored, source_total, complete, duplicates)
        )

    session_round.finish()

    print(format_summary(outcomes, email_found), file=out)

    return EXIT_INCOMPLETE if incomplete else EXIT_OK


class PostgrestCookiePot(CookiePot):
    """Productie-cookiepot tegen `simpul_raw.session_cookie` via PostgREST,
    met dezelfde on-conflict-aanpak als `PostgrestUpsertStore`. Voert geen DDL
    uit; het schema bestaat al of dit faalt (issue 11).

    De tabel draagt **één rij per cookie** — `name` is de primaire sleutel,
    `value` de waarde, `updated_at` het tijdstip — precies zoals issue 11 en
    het PRD hem beschrijven. Een eerdere versie schreef één rij `id=1` met een
    JSON-kolom `cookies`; die kolommen bestaan niet en elk verzoek liep op een
    PostgREST-fout. Q5/Q6/Q14 misten dat omdat de pot daar gestubd is.
    """

    TABLE = "session_cookie"

    def __init__(self, base_url: str, api_key: str, session: Any = None):
        self._base_url = postgrest_base_url(base_url)
        self._api_key = api_key
        if session is not None:
            self._session = session
        else:
            import requests

            self._session = requests.Session()

    def _headers(self, *, write: bool = False) -> Dict[str, str]:
        headers = {
            "apikey": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "Accept-Profile": SCHEMA,
        }
        if write:
            headers["Content-Type"] = "application/json"
            headers["Content-Profile"] = SCHEMA
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        return headers

    def read(self) -> Dict[str, str]:
        response = self._session.get(
            f"{self._base_url}/{self.TABLE}",
            params={"select": "name,value"},
            headers=self._headers(),
        )
        raise_for_write(response, self.TABLE, "lezen")
        rows = response.json() or []
        return {
            row["name"]: row["value"]
            for row in rows
            if row.get("name") and row.get("value") is not None
        }

    def write(self, cookies: Mapping[str, str]) -> None:
        if not cookies:
            return
        now = datetime.now(timezone.utc).isoformat()
        payload = [
            {"name": name, "value": value, "updated_at": now}
            for name, value in cookies.items()
        ]
        response = self._session.post(
            f"{self._base_url}/{self.TABLE}",
            json=payload,
            params={"on_conflict": "name"},
            headers=self._headers(write=True),
        )
        raise_for_write(response, self.TABLE, "upsert")


# De bron staat vast in het PRD: `https://schoutenhoveniers.simpul.nl`. Geen
# geheim, dus geen omgevingsvariabele die de PRD-`docker run` moet meegeven —
# die geeft alleen de vier geheimen door. `SIMPUL_BASE_URL` blijft als
# overschrijving bestaan (bijv. een acceptatiehost), maar een lege of
# ontbrekende waarde valt terug op de bron, niet op een lege basis: met een
# lege basis werd elk pad relatief en faalde de hele ronde.
DEFAULT_SIMPUL_BASE_URL = "https://schoutenhoveniers.simpul.nl"


def simpul_base_url(env: Mapping[str, str]) -> str:
    return (env.get("SIMPUL_BASE_URL") or DEFAULT_SIMPUL_BASE_URL).rstrip("/")


def _build_real_dependencies(env: Mapping[str, str]):
    import requests

    http_session = requests.Session()
    client = SimpulHTTPClient(http_session, base_url=simpul_base_url(env))
    username, password = credentials_from_env(env)
    store = postgrest_store_from_env(env)
    pot = PostgrestCookiePot(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SECRET_KEY", ""))
    return client, http_session, pot, store, username, password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simpul_extract",
        description="Simpul extractie-ronde.",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    parser.parse_args(argv)

    # Als eerste, vóór elke andere stap: een fout in het bedraden hoort ook
    # al een logregel te kunnen schrijven, en de geheimen uit de omgeving
    # moeten geregistreerd zijn voordat er iets te loggen valt.
    configure_logging(os.environ)

    try:
        client, http_session, pot, store, username, password = _build_real_dependencies(os.environ)
    except (SessionError, StorageError) as exc:
        logger.error("configuratiefout: %s", exc)
        print(f"configuratiefout: {exc}", file=sys.stderr)
        return 1

    return run(client=client, session=http_session, pot=pot, store=store, username=username, password=password)


if __name__ == "__main__":
    sys.exit(main())
