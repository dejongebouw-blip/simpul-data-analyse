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
        "last_seen_run",
        "missing_since",
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
        "fetched_at",
        "last_seen_run",
        "missing_since",
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
        "last_seen_run",
        "missing_since",
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

# issue 01 (SC-6): welke drie tabellen `last_seen_run` en `missing_since`
# dragen, en met welk type ze in de driftcontrole-array moeten staan. De
# bestaande database heeft deze tabellen al gevuld, dus alleen de `create
# table if not exists` zou stilzwijgend niets doen -- de `alter table ...
# add column if not exists` ernaast is wat de kolom er daadwerkelijk bij zet.
ENTITY_TABLES_WITH_TRACKING = ("customer", "project", "supplier")
TRACKING_COLUMNS = {
    "last_seen_run": "uuid",
    "missing_since": "timestamp with time zone",
}


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

    def test_complete_is_derived_by_the_database(self) -> None:
        """`extraction_run.complete` mag niet aanleverbaar zijn. Ronde 3 van
        2026-08-29 viel op PostgREST-fout 428C9 omdat de schrijflaag deze
        kolom meestuurde terwijl de database hem afleidt. De voorwaarde staat
        voluit -- zonder een door de bron gemeld totaal is de ronde niet
        bevestigd, en dat hoort `false` te zijn en niet NULL, zodat een latere
        query `= false` mag schrijven in plaats van `is not true`."""
        body = _table_body(self.code, "extraction_run")
        regel = next(
            line for line in body.splitlines() if line.strip().startswith("complete ")
        )
        genormaliseerd = " ".join(regel.lower().replace("(", " ").replace(")", " ").split())
        self.assertIn("generated always as", genormaliseerd)
        self.assertIn("stored", genormaliseerd)
        self.assertIn("source_total is not null", genormaliseerd)
        self.assertIn("rows_stored = source_total", genormaliseerd)

    def test_projecttypen_volgen_de_bron_niet_de_veldnaam(self) -> None:
        """Ronde 4 viel op `22P02 invalid input syntax for type integer:
        "Archief"`. `status_id` heet naar een id maar draagt een label: 2793
        van 2793 records uit de echte bron zijn tekst, nooit een getal. En
        `project_location` is geen adres maar een Fractal-object
        ({"data": []}), dus geen text. Beide typen zijn nagemeten, niet
        afgeleid uit de naam."""
        body = _table_body(self.code, "project")
        regels = {
            line.strip().split()[0]: line.strip().rstrip(",")
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("--")
        }
        self.assertEqual(regels["status_id"], "status_id text")
        self.assertEqual(regels["project_location"], "project_location jsonb")

    def test_invoiceable_amount_is_geen_kolom_meer(self) -> None:
        """De bron levert het bedrag null voor alle 2793 projecten, en de
        factuurmodule geeft dit account 403 op /invoice/invoiceable.json:
        "Dit heeft te maken met de rechten van uw account." Een kolom die
        nooit gevuld kan worden hoort niet in de definitie te staan. Komen de
        rechten er later, dan is de weg een eigen invoice-entiteit."""
        # Op `code`, niet op `sql`: de kopregels bewaren bewust de geschiedenis
        # van deze kolom, en die mag een pin op de definitie niet groen of rood
        # maken.
        self.assertNotIn("invoiceable_amount", self.code)

    def test_bestand_controleert_zelf_op_drift(self) -> None:
        """`create table if not exists` slaat een bestaande, afgedreven tabel
        stilzwijgend over: het bestand draait dan groen en verandert niets.
        Dat kostte op 2026-08-29 drie rondes tegen de echte bron. Er hoort dus
        een controle in te zitten die de werkelijke kolommen tegen de
        verklaarde legt en met een exception faalt als ze verschillen."""
        self.assertRegex(self.sql, r"(?is)\bdo\s*\$\$.*\braise\s+exception\b.*\$\$")
        self.assertIn("pg_attribute", self.sql)
        self.assertIn("format_type", self.sql)

    def test_last_seen_run_and_missing_since_added_idempotently(self) -> None:
        """De bestaande database heeft `customer`, `project` en `supplier`
        al gevuld staan wanneer deze kolommen erbij komen (SC-6). Alleen de
        `create table if not exists` zou stilzwijgend niets doen -- er hoort
        een `alter table ... add column if not exists` naast te staan, net
        als de aanleiding voor de driftcontrole zelf."""
        for table in ENTITY_TABLES_WITH_TRACKING:
            with self.subTest(table=table):
                self.assertRegex(
                    self.code,
                    rf"alter table simpul_raw\.{re.escape(table)} add column if not exists last_seen_run uuid",
                )
                self.assertRegex(
                    self.code,
                    rf"alter table simpul_raw\.{re.escape(table)} add column if not exists missing_since timestamptz",
                )

    def test_drift_control_covers_last_seen_run_and_missing_since(self) -> None:
        """De driftcontrole-array moet beide nieuwe kolommen dekken op alle
        drie de tabellen, met hetzelfde type als in de `create table`- en
        `alter table`-statements, zonder default, identity of afleiding."""
        for table in ENTITY_TABLES_WITH_TRACKING:
            for column, typ in TRACKING_COLUMNS.items():
                with self.subTest(table=table, column=column):
                    self.assertIn(f"['{table}','{column}','{typ}','','','f']", self.sql)

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

    def test_missing_last_seen_run_in_definition_is_detected(self) -> None:
        """Alleen `missing_since` toegevoegd, `last_seen_run` vergeten in de
        `create table`-definitie -- de kolomlijst-pin hoort dit rood te
        maken."""
        broken = """
        create table if not exists simpul_raw.customer (
            id bigint primary key,
            customer_number text,
            title text,
            address text,
            zipcode text,
            city text,
            phone text,
            mobile text,
            display_status text,
            tasks_status text,
            url_show text,
            email text,
            fetched_at timestamptz,
            missing_since timestamptz
        );
        """
        body = _table_body(broken, "customer")
        self.assertNotEqual(_column_names(body), EXPECTED_COLUMNS["customer"])

    def test_missing_tracking_column_in_drift_array_is_detected(self) -> None:
        """De definitie kreeg beide kolommen, maar de driftcontrole-array
        vergat `missing_since` op te nemen -- een database zonder die kolom
        zou dan stilzwijgend groen blijven in plaats van luid te falen."""
        broken_array_snippet = "['customer','last_seen_run','uuid','','','f'],"
        self.assertNotIn("['customer','missing_since','timestamp with time zone','','','f']", broken_array_snippet)

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
