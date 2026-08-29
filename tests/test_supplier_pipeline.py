"""Toetst de leveranciers-doorsnede uit issue 04 (US-2, US-3, SC-2, SC-3).

  - De pagineerlus doorloopt de Laravel-paginatorvorm van ``/supplier.json``
    tot het einde.
  - Rijen landen via de schrijfnaad — één operatie, ``upsert(table, rows)`` —
    in de tabel ``supplier``, met de kolomnamen uit ``schema-postgres.sql``
    en elk een ``fetched_at``.
  - Per run en per entiteit komt er een auditregel in ``extraction_run`` met
    tijdstip, opgeslagen rijen en het door de bron gemelde totaal.
  - Het extractiescript voert geen DDL uit.

Fixtures zijn synthetisch; geen enkel testgeval doet een echt netwerkverzoek.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import simpul_extract
from simpul_extract.pipeline import SUPPLIER_SPEC, extract_entity
from simpul_extract.storage import InMemoryWriter
from simpul_extract.suppliers import SUPPLIER_COLUMNS, parse_supplier_list
from tests._stubs import (
    OnlyUpsertWriter,
    PagedTransport,
    TickingClock,
    laravel_page,
    make_client,
    supplier_pages,
    synthetic_supplier,
)


def _extract(transport: PagedTransport, writer, **kwargs):
    return extract_entity(
        client=make_client(transport),
        writer=writer,
        spec=SUPPLIER_SPEC,
        run_id=kwargs.pop("run_id", "run-test"),
        now=kwargs.pop("now", TickingClock()),
        **kwargs,
    )


class TestPaginationWalksToEnd(unittest.TestCase):
    def test_all_pages_requested_in_order(self) -> None:
        transport = PagedTransport(supplier_pages(5, per_page=2))
        _extract(transport, InMemoryWriter())
        self.assertEqual(
            transport.requested_pages(),
            [1, 2, 3],
            msg="de lus moet elke pagina precies één keer opvragen, tot last_page",
        )

    def test_all_rows_across_pages_are_stored(self) -> None:
        writer = InMemoryWriter()
        transport = PagedTransport(supplier_pages(5, per_page=2))
        _extract(transport, writer)
        stored_ids = sorted(row["id"] for row in writer.rows("supplier"))
        self.assertEqual(stored_ids, [1, 2, 3, 4, 5])

    def test_single_page_list_stops_after_one_request(self) -> None:
        writer = InMemoryWriter()
        transport = PagedTransport(supplier_pages(3, per_page=50))
        _extract(transport, writer)
        self.assertEqual(transport.requested_pages(), [1])
        self.assertEqual(len(writer.rows("supplier")), 3)

    def test_requests_target_supplier_endpoint(self) -> None:
        transport = PagedTransport(supplier_pages(3))
        _extract(transport, InMemoryWriter())
        for url, _ in transport.calls:
            self.assertIn("/supplier.json", url)


class TestRowsLandViaTheSeam(unittest.TestCase):
    def test_pipeline_needs_nothing_beyond_upsert(self) -> None:
        writer = OnlyUpsertWriter()
        transport = PagedTransport(supplier_pages(4, per_page=2))
        _extract(transport, writer)
        tables = {table for table, _ in writer.calls}
        self.assertEqual(
            tables,
            {"supplier", "extraction_run"},
            msg="alle schrijfacties horen door upsert(table, rows) te gaan",
        )

    def test_rows_carry_schema_columns_and_fetched_at(self) -> None:
        writer = InMemoryWriter()
        transport = PagedTransport(supplier_pages(2))
        _extract(transport, writer)
        expected = set(SUPPLIER_COLUMNS) | {"fetched_at"}
        for row in writer.rows("supplier"):
            self.assertEqual(set(row), expected)
            self.assertTrue(row["fetched_at"], msg="elke rij draagt een fetched_at")

    def test_field_values_map_one_to_one(self) -> None:
        writer = InMemoryWriter()
        transport = PagedTransport(supplier_pages(1))
        _extract(transport, writer)
        (row,) = writer.rows("supplier")
        source = synthetic_supplier(1)
        for column in SUPPLIER_COLUMNS:
            self.assertEqual(row[column], source[column])

    def test_unknown_source_fields_are_ignored(self) -> None:
        item = synthetic_supplier(7)
        item["created_at"] = "2020-01-01"
        item["contacts"] = [{"name": "x"}]
        page = laravel_page(data=[item], current_page=1, last_page=1, total=1)
        writer = InMemoryWriter()
        _extract(PagedTransport({1: page}), writer)
        (row,) = writer.rows("supplier")
        self.assertNotIn("created_at", row)
        self.assertNotIn("contacts", row)


class TestParseSupplierList(unittest.TestCase):
    def test_accepts_full_laravel_payload(self) -> None:
        payload = laravel_page(
            data=[synthetic_supplier(1), synthetic_supplier(2)],
            current_page=1,
            last_page=1,
            total=2,
        )
        rows = parse_supplier_list(payload)
        self.assertEqual([row["id"] for row in rows], [1, 2])

    def test_accepts_plain_item_list(self) -> None:
        rows = parse_supplier_list([synthetic_supplier(9)])
        self.assertEqual(rows[0]["name"], "Leverancier 9")

    def test_rows_use_schema_column_names(self) -> None:
        rows = parse_supplier_list([synthetic_supplier(1)])
        self.assertEqual(set(rows[0]), set(SUPPLIER_COLUMNS))


class TestAuditTrail(unittest.TestCase):
    def test_one_audit_row_per_run_and_entity(self) -> None:
        writer = InMemoryWriter()
        _extract(PagedTransport(supplier_pages(5, per_page=2)), writer)
        audit_rows = writer.rows("extraction_run")
        self.assertEqual(len(audit_rows), 1)

    def test_audit_row_carries_counts_and_timestamps(self) -> None:
        writer = InMemoryWriter()
        clock = TickingClock()
        _extract(
            PagedTransport(supplier_pages(5, per_page=2)),
            writer,
            run_id="run-audit",
            now=clock,
        )
        (audit,) = writer.rows("extraction_run")
        self.assertEqual(audit["run_id"], "run-audit")
        self.assertEqual(audit["entity"], "supplier")
        self.assertEqual(audit["rows_stored"], 5)
        self.assertEqual(audit["source_total"], 5)
        self.assertTrue(audit["started_at"])
        self.assertTrue(audit["finished_at"])
        self.assertLess(audit["started_at"], audit["finished_at"])


class TestNoDDL(unittest.TestCase):
    DDL_PATTERN = re.compile(
        r"(?i)\b(create|alter|drop|truncate)\s+(table|schema|index|view)\b"
    )

    def test_package_source_contains_no_ddl(self) -> None:
        package_dir = Path(simpul_extract.__file__).resolve().parent
        for source_file in sorted(package_dir.glob("*.py")):
            with self.subTest(file=source_file.name):
                self.assertIsNone(
                    self.DDL_PATTERN.search(source_file.read_text()),
                    msg=(
                        f"{source_file.name} bevat DDL; het extractiescript "
                        "mag geen tabellen aanmaken of wijzigen (issue 09/10)"
                    ),
                )

    def test_writer_offers_no_ddl_or_delete_operations(self) -> None:
        writer = InMemoryWriter()
        for name in (
            "execute",
            "sql",
            "query",
            "create_table",
            "alter_table",
            "drop_table",
            "truncate",
            "delete",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    callable(getattr(writer, name, None)),
                    msg=f"de schrijfnaad mag geen `{name}` aanbieden",
                )


if __name__ == "__main__":
    unittest.main()
