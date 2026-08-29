"""Entry point voor het simpul_extract-container: bedraadt de lagen uit de
issues 03 t/m 09 tot één ronde over `customer`, `project` en `supplier`.

Vaste volgorde (zie ook `simpul_extract/session.py` voor de sessiestap):

  1. Cookiepot lezen en cookies injecteren.
  2. Sessie levend bewijzen; dood -> één loginpoging; mislukt -> EXIT_SESSION_LOST,
     niets geschreven.
  3. Per entiteit pagineren en parsen (nog niet wegschrijven).
  4. Relatiedetails ophalen voor `email` op de al geparste customer-rijen.
  5. Wegschrijven: elke entiteit in één upsert naar zijn tabel.
  6. Volledigheid toetsen per entiteit (rows_stored vs. het door de bron
     gemelde totaal) en de auditregel schrijven — ook bij een falende toets.
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

from simpul_extract.completeness import EXIT_INCOMPLETE, write_extraction_run
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
from simpul_extract.storage import (
    SCHEMA,
    StorageError,
    postgrest_base_url,
    postgrest_store_from_env,
)

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


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_summary(outcomes: Sequence[EntityOutcome], email_found: int) -> str:
    """Bouwt het slotoverzicht: per entiteit gevonden/gemeld plus ok, het
    aantal gevonden e-mailadressen en de sessiestatus. Draagt nooit een
    cookiewaarde of wachtwoord."""
    lines = [
        f"{outcome.entity}: gevonden {outcome.rows_stored}, gemeld {outcome.source_total} "
        f"({'ok' if outcome.complete else 'onvolledig'})"
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

    session_round = SessionRound(client, session, pot, username, password)
    session_round.start()
    try:
        session_round.ensure_live(probe_path)
    except SessionLostError:
        print("sessie: dood, login mislukt", file=out)
        return EXIT_SESSION_LOST

    collected: Dict[str, Dict[str, Any]] = {}
    for entity in ENTITIES:
        started_at = now()
        rows: List[Dict[str, Any]] = []
        source_total: Optional[int] = None
        for page in paginate(client, entity.path):
            if page.total is not None:
                source_total = page.total
            rows.extend(entity.parse(page.items))
        collected[entity.name] = {
            "rows": rows,
            "source_total": source_total,
            "started_at": started_at,
        }

    email_found = fetch_customer_emails(client, collected["customer"]["rows"])

    outcomes: List[EntityOutcome] = []
    incomplete = False
    for entity in ENTITIES:
        data = collected[entity.name]
        rows = data["rows"]
        store.upsert(entity.table, rows)
        rows_stored = len(rows)
        source_total = data["source_total"]
        complete = source_total is not None and rows_stored == source_total
        if not complete:
            incomplete = True
        finished_at = now()
        note = None if complete else f"{rows_stored} weggeschreven, bron meldt {source_total}"
        write_extraction_run(
            store,
            run_id=run_id,
            started_at=data["started_at"],
            finished_at=finished_at,
            entity=entity.name,
            rows_stored=rows_stored,
            source_total=source_total,
            complete=complete,
            note=note,
        )
        outcomes.append(EntityOutcome(entity.name, rows_stored, source_total, complete))

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
        response.raise_for_status()
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
        response.raise_for_status()


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

    try:
        client, http_session, pot, store, username, password = _build_real_dependencies(os.environ)
    except (SessionError, StorageError) as exc:
        print(f"configuratiefout: {exc}", file=sys.stderr)
        return 1

    return run(client=client, session=http_session, pot=pot, store=store, username=username, password=password)


if __name__ == "__main__":
    sys.exit(main())
