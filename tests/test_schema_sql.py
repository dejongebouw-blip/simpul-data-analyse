"""Toetst dat `db/schema-postgres.sql` het veldcontract van issue 11
letterlijk volgt: alle vijf tabellen, kolomnamen exact zoals in de
veldafbeelding, RLS aan op elke tabel zonder een enkele policy, en de
grants voor `anon`/`authenticated` ingetrokken (schema, bestaande tabellen,
en via `alter default privileges` ook toekomstige tabellen), en de
spiegelbeeldige grants die de secret-key-route (`service_role`) juist wel
openzetten.

De verwachte kolomlijsten hieronder zijn met de hand overgetypt uit issue 11,
onafhankelijk van het SQL-bestand — net als in issue 06
(`tests/test_field_contract.py`, zie ook
`lessons/2026-08-29-fixtures-uit-de-bron-niet-uit-het-hoofd.md`). Een stille
hernoeming zoals `customer.title` -> `customer.name`, of een ontbrekende RLS-
regel, maakt deze test rood zonder dat het SQL-bestand zelf intern
inconsistent hoeft te zijn.

Deze test leest alleen tekst; ze draait op de host zonder database of
netwerk.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema-postgres.sql"

EXPECTED_COLUMNS = {
    "customer": (
        "id",
        "customer_number",
        "title",
        "address",
        "zipcode",
        "city",
        "phone",
        "mobile",
        "display_status",
        "tasks_status",
        "url_show",
        "email",
        "fetched_at",
    ),
    "project": (
        "id",
        "project_number",
        "name",
        "customer_title",
        "customer_address",
        "customer_zipcode",
        "customer_city",
        "customer_phone",
        "customer_mobile",
        "url_show",
        "project_location",
        "status_id",
        "invoiceable_amount",
        "fetched_at",
    ),
    "supplier": (
        "id",
        "name",
        "address",
        "zipcode",
        "city",
        "email",
        "phone",
        "mobile",
        "url_show",
        "text",
        "fetched_at",
    ),
    "extraction_run": (
        "id",
        "run_id",
        "started_at",
        "finished_at",
        "entity",
        "rows_stored",
        "source_total",
        "complete",
        "note",
    ),
    "session_cookie": ("name", "value", "updated_at"),
}

TABLES = tuple(EXPECTED_COLUMNS)


def _strip_line_comments(sql: str) -> str:
    """Verwijdert `-- ...`-commentaar per regel, zodat een woord dat alleen
    in proza in een commentaarregel voorkomt (bijv. 'policy') geen match
    voor echte SQL oplevert."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _table_body(code: str, table: str) -> str:
    """Geeft de tekst tussen `create table if not exists simpul_raw.<table>
    (` en het bijbehorende sluit-`)` terug. Laat een AssertionError vallen
    als die tabel niet zo idempotent wordt aangemaakt."""
    pattern = re.compile(
        rf"create table if not exists simpul_raw\.{re.escape(table)}\s*\((.*?)\n\s*\)\s*;",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(code)
    if not match:
        raise AssertionError(
            f"geen idempotente 'create table if not exists simpul_raw.{table} (...)' gevonden"
        )
    return match.group(1)


def _column_names(body: str) -> tuple:
    names = []
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        names.append(line.split()[0])
    return tuple(names)


def _revoke_covers_both_roles(code: str, statement_prefix: str) -> bool:
    match = re.search(statement_prefix + r"([^;]*)", code, re.IGNORECASE)
    if not match:
        return False
    tail = match.group(1)
    return "anon" in tail and "authenticated" in tail


REVOKE_STATEMENTS = (
    r"revoke all on schema simpul_raw from",
    r"revoke all on all tables in schema simpul_raw from",
    r"alter default privileges in schema simpul_raw revoke all on tables from",
)

# De keerzijde van de revokes: zonder deze drie is het schema ook voor de
# secret key dicht en kan de schrijflaag geen rij wegschrijven. H2 (testplan)
# vond precies dat gat op de draaiende database -- de schema-ACL stond op
# `{postgres=UC/postgres}` en de nieuw aangemaakte `session_cookie` had alleen
# grants voor `postgres`. Deze test houdt het gat dicht in het bestand zelf.
SERVICE_ROLE_GRANTS = (
    r"grant usage on schema simpul_raw to",
    r"grant select, insert, update, delete on all tables in schema simpul_raw to",
    r"alter default privileges in schema simpul_raw grant select, insert, update, delete on tables to",
)


def _statement_targets_role(code: str, statement_prefix: str, role: str) -> bool:
    """True als het eerste statement met dit voorvoegsel `role` als doel heeft."""
    match = re.search(statement_prefix + r"([^;]*)", code, re.IGNORECASE)
    if not match:
        return False
    return role in match.group(1)


class TestSchemaFileExists(unittest.TestCase):
    def test_file_exists(self) -> None:
        self.assertTrue(SCHEMA_PATH.is_file(), f"{SCHEMA_PATH} ontbreekt")


class TestSchemaContract(unittest.TestCase):
    def setUp(self) -> None:
        raw = SCHEMA_PATH.read_text(encoding="utf-8")
        self.sql = raw
        self.code = _strip_line_comments(raw)

    def test_schema_created_idempotently(self) -> None:
        self.assertRegex(self.code, r"create schema if not exists simpul_raw\b")

    def test_all_five_tables_created_idempotently(self) -> None:
        for table in TABLES:
            with self.subTest(table=table):
                _table_body(self.code, table)

    def test_column_names_match_issue_literally(self) -> None:
        for table, expected in EXPECTED_COLUMNS.items():
            with self.subTest(table=table):
                body = _table_body(self.code, table)
                self.assertEqual(_column_names(body), expected)

    def test_rls_enabled_on_every_table(self) -> None:
        for table in TABLES:
            with self.subTest(table=table):
                self.assertRegex(
                    self.code,
                    rf"alter table simpul_raw\.{re.escape(table)} enable row level security",
                )

    def test_no_policy_is_created(self) -> None:
        self.assertNotRegex(self.code, r"create\s+policy")

    def test_grants_revoked_for_anon_and_authenticated(self) -> None:
        for statement in REVOKE_STATEMENTS:
            with self.subTest(statement=statement):
                self.assertTrue(
                    _revoke_covers_both_roles(self.code, statement),
                    f"revoke ontbreekt of dekt niet zowel anon als authenticated: {statement!r}",
                )

    def test_secret_key_route_is_granted_to_service_role(self) -> None:
        for statement in SERVICE_ROLE_GRANTS:
            with self.subTest(statement=statement):
                self.assertTrue(
                    _statement_targets_role(self.code, statement, "service_role"),
                    f"grant voor service_role ontbreekt: {statement!r}. Zonder deze "
                    f"grant is simpul_raw ook voor de secret key dicht en kan de "
                    f"schrijflaag niets wegschrijven.",
                )

    def test_service_role_grants_do_not_include_anon_or_authenticated(self) -> None:
        """De grants openen alleen de secret-key-route. Kwam `anon` of
        `authenticated` in een grantregel terecht, dan zou het schema juist
        opengaan voor de publishable key -- de fout die SC-9 uitsluit."""
        for statement in SERVICE_ROLE_GRANTS:
            for role in ("anon", "authenticated"):
                with self.subTest(statement=statement, role=role):
                    self.assertFalse(
                        _statement_targets_role(self.code, statement, role),
                        f"{role!r} staat in een grantregel: {statement!r}",
                    )


class TestCounterPin(unittest.TestCase):
    """Tegen-pin: bewijst dat de bovenstaande controles daadwerkelijk falen
    op de fouten uit de eerste poging, onafhankelijk van het echte
    SQL-bestand. Werkt op kleine, met de hand geschreven kapotte snippets."""

    def test_renamed_column_is_detected(self) -> None:
        broken = """
        create table if not exists simpul_raw.customer (
            id bigint primary key,
            customer_number text,
            name text,
            address text,
            zipcode text,
            city text,
            phone text,
            mobile text,
            display_status text,
            tasks_status text,
            url_show text,
            email text,
            fetched_at timestamptz
        );
        """
        body = _table_body(broken, "customer")
        self.assertNotEqual(_column_names(body), EXPECTED_COLUMNS["customer"])

    def test_missing_rls_is_detected(self) -> None:
        broken = """
        create table if not exists simpul_raw.supplier (
            id bigint primary key,
            name text
        );
        alter table simpul_raw.customer enable row level security;
        """
        self.assertNotRegex(
            broken,
            r"alter table simpul_raw\.supplier enable row level security",
        )

    def test_create_policy_is_detected(self) -> None:
        broken = "create policy supplier_open on simpul_raw.supplier for select using (true);"
        self.assertRegex(broken, r"create\s+policy")

    def test_missing_revoke_is_detected(self) -> None:
        broken = "revoke all on schema simpul_raw from anon;"
        self.assertFalse(
            _revoke_covers_both_roles(broken, r"revoke all on schema simpul_raw from")
        )

    def test_missing_service_role_grant_is_detected(self) -> None:
        """De toestand van vóór H2: wel afsluiten, niet openzetten."""
        broken = "revoke all on schema simpul_raw from anon, authenticated;"
        self.assertFalse(
            _statement_targets_role(
                broken, r"grant usage on schema simpul_raw to", "service_role"
            )
        )

    def test_grant_to_anon_is_detected(self) -> None:
        """De omgekeerde fout: het schema per ongeluk openzetten voor de
        publishable key."""
        broken = "grant usage on schema simpul_raw to anon, service_role;"
        self.assertTrue(
            _statement_targets_role(
                broken, r"grant usage on schema simpul_raw to", "anon"
            )
        )


if __name__ == "__main__":
    unittest.main()
