"""Schrijflaag naar `simpul_raw`: idempotente upsert achter één interface.

`upsert(table, rows)` is de naad die de rest van de ronde (paginering,
parsers) van Postgres scheidt. Twee implementaties voldoen eraan:

- `InMemoryUpsertStore` — een in-memory nepimplementatie voor de tests in
  `tests/test_persistence.py`. Geen Postgres, geen netwerk.
- `PostgrestUpsertStore` — de echte implementatie tegen PostgREST op
  `SUPABASE_URL`/`SUPABASE_SECRET_KEY`, schema `simpul_raw`, via
  `INSERT ... ON CONFLICT (id) DO UPDATE`. Wordt in issue 07 niet tegen een
  echte database aangetoond: dat is handwerk in het testplan, geen
  schrijfverzoek vanuit een test naar een draaiende omgeving (durable
  regel 5).

Deze module voert nergens DDL uit; het schema bestaat al of dit faalt
(issue 11).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Sequence

from simpul_extract.observability import describe_http_response, get_logger

logger = get_logger(__name__)

SCHEMA = "simpul_raw"


class StorageError(Exception):
    """Basisfout van de schrijflaag."""


class StorageWriteError(StorageError):
    """Een schrijfverzoek werd door PostgREST geweigerd.

    Draagt de reden mee. `response.raise_for_status()` deed dat niet: die gaf
    `400 Client Error: Bad Request for url: ...` en gooide de responsebody weg,
    terwijl juist die body vertelt wélke kolom of welk type niet klopt. Drie
    defecten op de `extraction_run`-naad waren daardoor alleen te vinden door
    een hele ronde opnieuw te draaien.
    """

    def __init__(self, table: str, operation: str, response: Any):
        self.table = table
        self.operation = operation
        self.status = getattr(response, "status_code", None)
        self.response = response
        super().__init__(
            f"{operation} naar {SCHEMA}.{table} geweigerd: "
            f"{describe_http_response(response)}"
        )


def raise_for_write(response: Any, table: str, operation: str) -> None:
    """Vervangt `response.raise_for_status()` in de schrijflaag: logt en werpt
    een fout die zijn eigen reden draagt."""
    status = getattr(response, "status_code", 0) or 0
    if 200 <= status < 300:
        logger.info("%s %s.%s ok (status %s)", operation, SCHEMA, table, status)
        return
    error = StorageWriteError(table, operation, response)
    logger.error("%s", error)
    raise error


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UpsertResult:
    """Telling van één upsert-aanroep: hoeveel rijen zijn toegevoegd, en
    hoeveel bestaande rijen zijn bijgewerkt omdat een veldwaarde veranderde.
    Een rij die ongewijzigd opnieuw wordt aangeboden telt in geen van beide
    mee — dat is precies wat SC-4 vereist van een tweede, ongewijzigde
    ronde."""

    inserted: int
    updated: int


class UpsertStore:
    """Injecteerbare interface voor de schrijflaag.

    Concrete implementaties (`InMemoryUpsertStore` en `PostgrestUpsertStore`)
    en teststubs voldoen hieraan door `upsert()` te implementeren; verdere
    contractcontrole is niet nodig omdat Python duck-typet.
    """

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        raise NotImplementedError

    def append(self, table: str, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        """Voegt rijen toe aan een tabel die géén `id` van ons krijgt en géén
        `fetched_at` draagt — de auditlog `extraction_run` (issue 11: `id`
        bigint *generated always as identity*, en expliciet geen `fetched_at`).

        Een auditregel is een toevoeging, geen upsert: er is niets om op te
        conflicteren. `upsert()` gebruiken brak hier tweemaal tegelijk — het
        stuurde een tekstsleutel naar een identity-kolom én een `fetched_at`
        die de tabel niet heeft.
        """
        raise NotImplementedError


class InMemoryUpsertStore(UpsertStore):
    """In-memory nepimplementatie: geen Postgres, geen netwerk.

    Houdt per tabel een dict van `id` naar rij bij. Een tweede schrijfactie
    met een `id` die al bestaat overschrijft de rij (DO UPDATE) in plaats van
    een tweede rij toe te voegen — dat is de naad die de idempotentietoets
    zonder Postgres bewijst. `fetched_at` wordt op elke rij bij elke
    schrijfactie ververst, ook als de overige velden niet veranderen: het
    weerspiegelt wanneer de rij voor het laatst gezien is, niet of ze
    veranderd is.
    """

    def __init__(self, now: Callable[[], str] = _default_now):
        self._now = now
        self.tables: Dict[str, Dict[Any, Dict[str, Any]]] = {}
        self._next_identity: Dict[str, int] = {}

    def append(self, table: str, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        """Bootst de identity-kolom na: elke rij krijgt een oplopend geheel
        getal als sleutel, precies zoals Postgres dat doet. De rij zelf blijft
        onaangeraakt — geen `id`, geen `fetched_at`."""
        existing = self.tables.setdefault(table, {})
        for row in rows:
            volgnummer = self._next_identity.get(table, 1)
            self._next_identity[table] = volgnummer + 1
            existing[volgnummer] = dict(row)
        return UpsertResult(inserted=len(rows), updated=0)

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        existing = self.tables.setdefault(table, {})
        inserted = 0
        updated = 0
        # Dezelfde weigering als Postgres: één upsert-command mag dezelfde rij
        # niet twee keer raken (21000, "ON CONFLICT DO UPDATE command cannot
        # affect row a second time"). Tot 2026-08-29 slikte deze nep-store zo'n
        # batch stilzwijgend -- de tweede rij overschreef gewoon de eerste --
        # en dus kon geen enkele test zien dat de bron 15 projecten dubbel
        # levert. Ronde 5 tegen de echte database zag het wel. Een naad die
        # meer toelaat dan het echte ding toetst niets.
        gezien = set()
        for row in rows:
            if "id" in row:
                if row["id"] in gezien:
                    raise StorageError(
                        f"upsert naar {table!r} biedt id {row['id']!r} meer dan eens "
                        f"aan in dezelfde batch; Postgres weigert dat met 21000"
                    )
                gezien.add(row["id"])
        for row in rows:
            if "id" not in row:
                raise StorageError(
                    f"rij zonder 'id' kan niet upserted worden naar {table!r}: {row!r}"
                )
            row_id = row["id"]
            fetched_at = self._now()
            if row_id not in existing:
                stamped = dict(row)
                stamped["fetched_at"] = fetched_at
                existing[row_id] = stamped
                inserted += 1
                continue

            previous = existing[row_id]
            previous_without_stamp = {k: v for k, v in previous.items() if k != "fetched_at"}
            if previous_without_stamp != dict(row):
                stamped = dict(row)
                stamped["fetched_at"] = fetched_at
                existing[row_id] = stamped
                updated += 1
            else:
                previous["fetched_at"] = fetched_at

        return UpsertResult(inserted=inserted, updated=updated)


class PostgrestUpsertStore(UpsertStore):
    """Echte schrijflaag tegen PostgREST, schema `simpul_raw`.

    Stuurt één POST per tabel per ronde met `Prefer: resolution=merge-
    duplicates` en `on_conflict=id`, wat PostgREST vertaalt naar
    `INSERT ... ON CONFLICT (id) DO UPDATE`. `session` is injecteerbaar
    (net als bij `SimpulHTTPClient`) zodat de aanroep zelf met een stub te
    toetsen is zonder het echte Supabase-project te raken; standaard wordt
    een echte `requests.Session` gebruikt.

    PostgREST met `return=minimal` meldt niet welke rijen ingevoegd versus
    bijgewerkt zijn, dus deze implementatie rapporteert het totaal onder
    `inserted` met `updated=0`. Dat is een bewuste versimpeling: de exacte
    telling is alleen getoetst tegen de nepimplementatie (SC-4).
    """

    def __init__(self, base_url: str, api_key: str, session=None, now: Callable[[], str] = _default_now):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._now = now
        if session is not None:
            self._session = session
        else:
            import requests

            self._session = requests.Session()

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        if not rows:
            return UpsertResult(inserted=0, updated=0)

        payload = [dict(row, fetched_at=self._now()) for row in rows]
        logger.info(
            "upsert %s.%s: %d rijen, kolommen %s",
            SCHEMA, table, len(payload), sorted(payload[0].keys()),
        )
        response = self._session.post(
            f"{self._base_url}/{table}",
            json=payload,
            params={"on_conflict": "id"},
            headers={
                "apikey": self._api_key,
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Content-Profile": SCHEMA,
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        raise_for_write(response, table, "upsert")
        return UpsertResult(inserted=len(payload), updated=0)

    def append(self, table: str, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        """Platte insert: geen `on_conflict`, geen `resolution=merge-
        duplicates` en géén toegevoegde `fetched_at`. Postgres vult de
        identity-kolom zelf."""
        if not rows:
            return UpsertResult(inserted=0, updated=0)

        logger.info(
            "append %s.%s: %d rijen, kolommen %s",
            SCHEMA, table, len(rows), sorted(rows[0].keys()),
        )
        response = self._session.post(
            f"{self._base_url}/{table}",
            json=[dict(row) for row in rows],
            headers={
                "apikey": self._api_key,
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Content-Profile": SCHEMA,
                "Prefer": "return=minimal",
            },
        )
        raise_for_write(response, table, "append")
        return UpsertResult(inserted=len(rows), updated=0)


REST_PATH = "/rest/v1"


def postgrest_base_url(supabase_url: str) -> str:
    """Maakt van de Supabase-projecturl het PostgREST-eindpunt.

    `SUPABASE_URL` is bij Supabase de projecturl zonder pad
    (`https://<ref>.supabase.co`); PostgREST hangt daar onder `/rest/v1`. De
    schrijflaag plakt de tabelnaam achter deze basis, dus zonder `/rest/v1`
    landt een upsert op de projectroot en geeft 404. Een url die het pad al
    draagt blijft ongemoeid, zodat een expliciet gezette basis blijft werken.
    """
    base = (supabase_url or "").rstrip("/")
    if base.endswith(REST_PATH):
        return base
    return base + REST_PATH


def postgrest_store_from_env(env: Mapping[str, str]) -> PostgrestUpsertStore:
    """Bouwt de echte schrijflaag uit `SUPABASE_URL`/`SUPABASE_SECRET_KEY`
    in `env` (bijv. `os.environ`)."""
    base_url = env.get("SUPABASE_URL")
    api_key = env.get("SUPABASE_SECRET_KEY")
    if not base_url or not api_key:
        raise StorageError(
            "SUPABASE_URL en SUPABASE_SECRET_KEY zijn beide vereist voor de schrijflaag"
        )
    return PostgrestUpsertStore(postgrest_base_url(base_url), api_key)
